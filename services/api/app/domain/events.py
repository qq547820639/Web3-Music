import uuid
import psycopg2.extras

def emit(cur,event_type: str,aggregate_type: str,aggregate_id: str,payload:dict,workspace_id: str|None=None):
    event_id=str(uuid.uuid4())
    cur.execute("INSERT INTO domain_outbox(id,workspace_id,aggregate_type,aggregate_id,event_type,payload) VALUES(%s,%s,%s,%s,%s,%s)",
                (event_id,workspace_id,aggregate_type,aggregate_id,event_type,psycopg2.extras.Json(payload)))
    return event_id
