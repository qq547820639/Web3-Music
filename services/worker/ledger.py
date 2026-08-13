from decimal import Decimal
import uuid
import psycopg2.extras

def d(x):return Decimal(str(x))

def _g(row, key, idx):
    # The worker calls ledger functions with a RealDictCursor (dict rows).
    # Keep a tuple-index fallback so a plain cursor still works if reused.
    return row[key] if isinstance(row, dict) else row[idx]

def accounts(cur,workspace):
    cur.execute("SELECT id,account_type FROM ledger_accounts WHERE workspace_id=%s FOR UPDATE",(workspace,))
    return {_g(r,"account_type",1):str(_g(r,"id",0)) for r in cur.fetchall()}

def post(cur,workspace,op,kind,ref,pairs,actor="worker",meta=None):
    pairs=[(a,d(v)) for a,v in pairs]
    cur.execute("SELECT id,transaction_type,reference_type,reference_id FROM ledger_transactions WHERE workspace_id=%s AND operation_key=%s FOR UPDATE",(workspace,op));row=cur.fetchone()
    if row:
        tx_id=str(_g(row,"id",0))
        cur.execute("SELECT a.account_type,e.delta FROM ledger_entries e JOIN ledger_accounts a ON a.id=e.account_id WHERE e.transaction_id=%s ORDER BY a.account_type",(tx_id,))
        actual=sorted((_g(r,"account_type",0),d(_g(r,"delta",1))) for r in cur.fetchall())
        if _g(row,"transaction_type",1)!=kind or _g(row,"reference_type",2)!="generation_job" or str(_g(row,"reference_id",3))!=str(ref) or actual!=sorted(pairs):
            raise RuntimeError("operation key reused with different ledger semantics")
        return tx_id,False
    if sum((v for _,v in pairs),Decimal(0))!=0:raise RuntimeError("unbalanced ledger transaction")
    ids=accounts(cur,workspace);tx=str(uuid.uuid4())
    cur.execute("INSERT INTO ledger_transactions(id,workspace_id,operation_key,transaction_type,reference_type,reference_id,metadata,created_by) VALUES(%s,%s,%s,%s,'generation_job',%s,%s,%s)",(tx,workspace,op,kind,ref,psycopg2.extras.Json(meta or {}),actor))
    for a,v in pairs:cur.execute("INSERT INTO ledger_entries(workspace_id,transaction_id,account_id,delta) VALUES(%s,%s,%s,%s)",(workspace,tx,ids[a],v))
    return tx,True

def close_hold(cur,workspace,hold_id,settle,job_id):
    cur.execute("SELECT original_amount,settled_amount,released_amount,status FROM credit_holds WHERE id=%s AND workspace_id=%s FOR UPDATE",(hold_id,workspace));row=cur.fetchone()
    if not row:raise RuntimeError("hold missing")
    original,settled,released,status=d(_g(row,"original_amount",0)),d(_g(row,"settled_amount",1)),d(_g(row,"released_amount",2)),_g(row,"status",3)
    remaining=original-settled-released;target=d(settle)
    if target<0 or target>remaining:raise RuntimeError("settlement exceeds hold")
    release=remaining-target
    if target:post(cur,workspace,f"settle:{job_id}","settle",job_id,[("held",-target),("expense",target)],meta={"hold_id":hold_id})
    if release:post(cur,workspace,f"release:{job_id}","release",job_id,[("held",-release),("available",release)],meta={"hold_id":hold_id})
    ns=settled+target;nr=released+release;new_status="settled" if ns==original else "released" if nr==original else "partially_settled"
    cur.execute("UPDATE credit_holds SET settled_amount=%s,released_amount=%s,status=%s,updated_at=now() WHERE id=%s",(ns,nr,new_status,hold_id))
    return int(target),int(release),new_status
