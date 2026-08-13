from decimal import Decimal
import uuid
import psycopg2.extras

class InsufficientCredits(ValueError): pass
class LedgerConflict(ValueError): pass

ACCOUNT_TYPES=("available","held","expense","refunds","issued")

def _d(value): return Decimal(str(value))

def balances(cur,workspace_id: str) -> dict[str,float]:
    cur.execute("SELECT account_type,balance FROM ledger_balances WHERE workspace_id=%s",(workspace_id,))
    out={x:0.0 for x in ACCOUNT_TYPES}
    for row in cur.fetchall():
        account_type=row[0] if not isinstance(row,dict) else row["account_type"]
        balance=row[1] if not isinstance(row,dict) else row["balance"]
        out[account_type]=float(balance)
    return out


def _accounts(cur,workspace_id: str):
    cur.execute("SELECT id,account_type FROM ledger_accounts WHERE workspace_id=%s FOR UPDATE",(workspace_id,))
    return {(r[1] if not isinstance(r,dict) else r["account_type"]):str(r[0] if not isinstance(r,dict) else r["id"]) for r in cur.fetchall()}


def post(cur,workspace_id: str,operation_key: str,transaction_type: str,reference_type: str,reference_id: str,pairs:list[tuple[str,Decimal|int|float]],created_by: str,metadata=None):
    normalized=[(name,_d(delta)) for name,delta in pairs]
    cur.execute("SELECT id,transaction_type,reference_type,reference_id FROM ledger_transactions WHERE workspace_id=%s AND operation_key=%s FOR UPDATE",(workspace_id,operation_key))
    existing=cur.fetchone()
    if existing:
        get=lambda key,index: existing[key] if isinstance(existing,dict) else existing[index]
        tx_id=str(get("id",0))
        cur.execute("SELECT a.account_type,e.delta FROM ledger_entries e JOIN ledger_accounts a ON a.id=e.account_id WHERE e.transaction_id=%s ORDER BY a.account_type",(tx_id,))
        actual=sorted((r["account_type"] if isinstance(r,dict) else r[0],_d(r["delta"] if isinstance(r,dict) else r[1])) for r in cur.fetchall())
        expected=sorted(normalized)
        if get("transaction_type",1)!=transaction_type or get("reference_type",2)!=reference_type or str(get("reference_id",3))!=str(reference_id) or actual!=expected:
            raise LedgerConflict("operation key reused with different ledger semantics")
        return tx_id,False
    if sum((delta for _,delta in normalized),Decimal("0"))!=0: raise LedgerConflict("transaction is not balanced")
    ids=_accounts(cur,workspace_id); tx=str(uuid.uuid4())
    cur.execute("INSERT INTO ledger_transactions(id,workspace_id,operation_key,transaction_type,reference_type,reference_id,metadata,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (tx,workspace_id,operation_key,transaction_type,reference_type,reference_id,psycopg2.extras.Json(metadata or {}),created_by))
    for account_type,delta in normalized:
        if account_type not in ids: raise LedgerConflict(f"missing account {account_type}")
        cur.execute("INSERT INTO ledger_entries(workspace_id,transaction_id,account_id,delta) VALUES(%s,%s,%s,%s)",(workspace_id,tx,ids[account_type],delta))
    return tx,True


def create_hold(cur,workspace_id: str,quote_id: str,amount:int,expires_at,created_by: str):
    cur.execute("SELECT id,original_amount,settled_amount,released_amount,status FROM credit_holds WHERE workspace_id=%s AND quote_id=%s FOR UPDATE",(workspace_id,quote_id))
    row=cur.fetchone()
    if row: return str(row[0] if not isinstance(row,dict) else row["id"]),False
    _accounts(cur,workspace_id)  # serialize all holds for the workspace before checking balance
    current=balances(cur,workspace_id)
    if current["available"]<amount: raise InsufficientCredits(f"available={current['available']}, required={amount}")
    hold_id=str(uuid.uuid4())
    post(cur,workspace_id,f"hold:{quote_id}","hold","generation_quote",quote_id,[("available",-amount),("held",amount)],created_by,{"hold_id":hold_id})
    cur.execute("INSERT INTO credit_holds(id,workspace_id,quote_id,original_amount,expires_at) VALUES(%s,%s,%s,%s,%s)",(hold_id,workspace_id,quote_id,amount,expires_at))
    return hold_id,True


def close_hold(cur,workspace_id: str,hold_id: str,settle_amount:int,reference_id: str,created_by: str):
    cur.execute("SELECT * FROM credit_holds WHERE id=%s AND workspace_id=%s FOR UPDATE",(hold_id,workspace_id)); row=cur.fetchone()
    if not row: raise LedgerConflict("hold not found")
    get=lambda k,i: row[k] if isinstance(row,dict) else row[i]
    original=_d(get("original_amount",3)); settled=_d(get("settled_amount",4)); released=_d(get("released_amount",5))
    remaining=original-settled-released
    target=_d(settle_amount)
    if target<0 or target>remaining: raise LedgerConflict("settlement exceeds remaining hold")
    release=remaining-target
    if target:
        post(cur,workspace_id,f"settle:{reference_id}","settle","generation_job",reference_id,[("held",-target),("expense",target)],created_by,{"hold_id":hold_id})
    if release:
        post(cur,workspace_id,f"release:{reference_id}","release","generation_job",reference_id,[("held",-release),("available",release)],created_by,{"hold_id":hold_id})
    new_settled=settled+target; new_released=released+release
    status="settled" if new_settled==original else "released" if new_released==original else "partially_settled"
    cur.execute("UPDATE credit_holds SET settled_amount=%s,released_amount=%s,status=%s,updated_at=now() WHERE id=%s",(new_settled,new_released,status,hold_id))
    return {"settled":float(target),"released":float(release),"status":status}


def refund(cur,workspace_id: str,job_id: str,amount:int,created_by: str):
    if amount<=0: return None
    return post(cur,workspace_id,f"refund:{job_id}","refund","generation_job",job_id,[("expense",-amount),("available",amount)],created_by,{"original_job_id":job_id})
