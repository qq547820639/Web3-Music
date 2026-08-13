from decimal import Decimal
import uuid
import psycopg2.extras

def d(x):return Decimal(str(x))
def accounts(cur,workspace):
    cur.execute("SELECT id,account_type FROM ledger_accounts WHERE workspace_id=%s FOR UPDATE",(workspace,));return {r[1]:str(r[0]) for r in cur.fetchall()}
def post(cur,workspace,op,kind,ref,pairs,actor="worker",meta=None):
    pairs=[(a,d(v)) for a,v in pairs]
    cur.execute("SELECT id,transaction_type,reference_type,reference_id FROM ledger_transactions WHERE workspace_id=%s AND operation_key=%s FOR UPDATE",(workspace,op));row=cur.fetchone()
    if row:
        tx_id=str(row[0])
        cur.execute("SELECT a.account_type,e.delta FROM ledger_entries e JOIN ledger_accounts a ON a.id=e.account_id WHERE e.transaction_id=%s ORDER BY a.account_type",(tx_id,))
        actual=sorted((r[0],d(r[1])) for r in cur.fetchall())
        if row[1]!=kind or row[2]!="generation_job" or str(row[3])!=str(ref) or actual!=sorted(pairs):
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
    original,settled,released,status=d(row[0]),d(row[1]),d(row[2]),row[3]
    remaining=original-settled-released;target=d(settle)
    if target<0 or target>remaining:raise RuntimeError("settlement exceeds hold")
    release=remaining-target
    if target:post(cur,workspace,f"settle:{job_id}","settle",job_id,[("held",-target),("expense",target)],meta={"hold_id":hold_id})
    if release:post(cur,workspace,f"release:{job_id}","release",job_id,[("held",-release),("available",release)],meta={"hold_id":hold_id})
    ns=settled+target;nr=released+release;new_status="settled" if ns==original else "released" if nr==original else "partially_settled"
    cur.execute("UPDATE credit_holds SET settled_amount=%s,released_amount=%s,status=%s,updated_at=now() WHERE id=%s",(ns,nr,new_status,hold_id))
    return int(target),int(release),new_status
