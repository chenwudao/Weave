"""Project-local, resumable updates shared by project conversations and Bots."""
from contextlib import closing
from datetime import datetime, timezone
import json

import workspace


def ensure(db):
    db.execute("""CREATE TABLE IF NOT EXISTS labweave_project_flow(
        seq INTEGER PRIMARY KEY, project TEXT NOT NULL, event_key TEXT NOT NULL,
        kind TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(project,event_key))""")
    db.execute("""CREATE INDEX IF NOT EXISTS labweave_project_flow_scope
        ON labweave_project_flow(project,seq)""")


def append(db, project, key, kind, payload):
    db.execute("""INSERT INTO labweave_project_flow(project,event_key,kind,payload)
        VALUES(?,?,?,?) ON CONFLICT(project,event_key) DO NOTHING""",
               (project, key, kind, json.dumps(payload, ensure_ascii=False)))


def record_change(db, project, rail, record, action):
    if project == "global" or rail in {"chat", "feed"}:
        return
    ensure(db)
    identity = record["id"]
    updated = record.get("updatedAt") or record.get("updated") or record.get("ts") or datetime.now(timezone.utc).isoformat()
    payload = {"recordId": identity, "rail": rail, "action": action, "updatedAt": updated}
    if action != "removed":
        payload["title"] = workspace.text(record.get("title") or record.get("name") or record.get("text"), 180).split("\n", 1)[0]
        payload["summary"] = workspace.text(record.get("summary") or record.get("text"), 420)
        for key in ("status", "done", "featureId", "taskId", "metrics"):
            if key in record:
                payload[key] = record[key]
        sources = record.get("sources")
        if isinstance(sources, list):
            source_ids = [source["recordId"] for source in sources if isinstance(source, dict)
                          and isinstance(source.get("recordId"), str)]
            if source_ids:
                payload["sourceRecordIds"] = source_ids[:12]
    append(db, project, f"artifact:{rail}:{identity}:{action}:{updated}", "artifact", payload)


def write(project, data):
    session = workspace.text(data.get("sessionId"), 160)
    message = workspace.text(data.get("messageId"), 200)
    body = workspace.text(data.get("text"), 4000)
    if not session or not message or not body:
        raise workspace.WorkspaceError(400, "需要项目对话、消息与共享内容。")
    data_fields = {
        name: workspace.text(data.get(name), 400)
        for name in ("botId", "handoffId", "sourceSessionId", "followUpOf")
        if isinstance(data.get(name), str) and data[name]
    }
    kind = "decision" if data.get("action") == "decision" else "input"
    with closing(workspace.database()) as db, db:
        ensure(db)
        append(db, project, kind + ":" + session + ":" + message, kind,
               {"sessionId": session, "messageId": message, "text": body,
                "createdAt": datetime.now(timezone.utc).isoformat(), **data_fields})
        if kind == "input" and data.get("checkPinnedRules") is True:
            pinned = db.execute("SELECT 1 FROM memories WHERE project=? AND category='rule' AND pinned=1 LIMIT 1",
                                (project,)).fetchone()
            return {"ok": True, "hasPinnedRules": bool(pinned)}
    return {"ok": True}


def publish_coordination(project, data):
    session = workspace.text(data.get("sessionId"), 160)
    request = workspace.text(data.get("requestId"), 200)
    key = workspace.text(data.get("updateId"), 100)
    body = {field: workspace.text(data.get(field), limit) for field, limit in
            (("title", 100), ("summary", 500), ("nextStep", 240))}
    if not session or not request or not key or not all(body.values()):
        raise workspace.WorkspaceError(400, "请提供任务理解、安排依据和下一步。")
    with closing(workspace.database()) as db, db:
        ensure(db)
        if not db.execute("SELECT 1 FROM labweave_project_flow WHERE project=? AND event_key=? AND kind='input'",
                          (project, f"input:{session}:{request}")).fetchone():
            raise workspace.WorkspaceError(409, "这项更新需要关联当前项目对话。")
        append(db, project, f"coordination:{session}:{request}:{key}", "coordination",
               {"sessionId": session, "requestId": request, **body})
    return {"ok": True}


def coordination_updates(project, session):
    with closing(workspace.database()) as db:
        ensure(db)
        rows = db.execute("""SELECT seq,payload FROM labweave_project_flow
            WHERE project=? AND kind='coordination' AND json_extract(payload,'$.sessionId')=?
              AND json_extract(payload,'$.requestId')=(
                SELECT json_extract(i.payload,'$.messageId') FROM labweave_project_flow i
                WHERE i.project=? AND i.kind='input' AND json_extract(i.payload,'$.sessionId')=?
                ORDER BY i.seq DESC LIMIT 1)
            ORDER BY seq DESC LIMIT 5""", (project, session, project, session)).fetchall()
    return [{"id": seq, **json.loads(payload)} for seq, payload in reversed(rows)]


def paper_activity(project, data):
    session = workspace.text(data.get("sessionId"), 160)
    key = workspace.text(data.get("activityId"), 100)
    query = workspace.text(data.get("query"), 300)
    status = data.get("status")
    if not session or not key or not query or status not in ("running", "completed", "failed", "cancelled"):
        raise workspace.WorkspaceError(400, "无效的论文检索进展。")
    titles = data.get("titles", [])
    payload = {"sessionId": session, "taskId": workspace.text(data.get("taskId"), 100),
               "botId": workspace.text(data.get("botId"), 100), "query": query, "label": workspace.text(data.get("label"), 24) or "搜索论文",
               "status": status, "ts": datetime.now(timezone.utc).isoformat(),
               "count": data.get("count"), "titles": [workspace.text(t, 250) for t in titles[:4]]}
    with closing(workspace.database()) as db, db:
        ensure(db)
        db.execute("""INSERT INTO labweave_project_flow(project,event_key,kind,payload)
            VALUES(?,?,'paper-activity',?) ON CONFLICT(project,event_key) DO UPDATE SET payload=excluded.payload
            WHERE json_extract(labweave_project_flow.payload,'$.status')='running'""",
                   (project, f"paper:{session}:{key}", json.dumps(payload, ensure_ascii=False)))
    return {"ok": True}


def paper_activities(db, project, *, session=None, task=None, bot=None, after=0):
    field, value = ("botId", bot) if bot is not None else ("taskId", task) if task is not None else ("sessionId", session)
    direct = "AND json_extract(payload,'$.taskId')=''" if bot is not None else ""
    rows = db.execute(f"""SELECT seq,payload FROM labweave_project_flow WHERE project=? AND kind='paper-activity'
            AND seq>? AND json_extract(payload,'$.{field}')=? {direct} ORDER BY seq DESC LIMIT 12""",
                      (project, after, value)).fetchall()
    return [{"id": seq, **json.loads(payload)} for seq, payload in reversed(rows)]


def conversation_paper_activities(project, session):
    with closing(workspace.database()) as db:
        ensure(db)
        cursor = db.execute("""SELECT coalesce(max(seq),0) FROM labweave_project_flow
            WHERE project=? AND kind='input' AND json_extract(payload,'$.sessionId')=?""", (project, session)).fetchone()[0]
        return paper_activities(db, project, session=session, after=cursor)


def current_version(db, project):
    return db.execute("""SELECT coalesce(max(seq),0) FROM labweave_project_flow
        WHERE project=? AND kind='decision'""", (project,)).fetchone()[0]


def record_file_changes(project, data):
    session = workspace.text(data.get("sessionId"), 160)
    message = workspace.text(data.get("messageId"), 200)
    files = data.get("files")
    if not session or not message or not isinstance(files, list) or not 1 <= len(files) <= 100:
        raise workspace.WorkspaceError(400, "需要明确的项目对话和文件变更。")
    validated = []
    for item in files:
        path = item.get("path") if isinstance(item, dict) else None
        if (not isinstance(path, str) or len(path) > 1000 or "\\" in path or ":" in path or "\x00" in path
                or any(part in ("", ".", "..", ".git") for part in path.split("/"))
                or item.get("action") not in ("added", "changed", "removed")):
            raise workspace.WorkspaceError(400, "文件必须位于项目目录内。")
        validated.append({"path": path, "action": item["action"]})
    with closing(workspace.database()) as db, db:
        ensure(db)
        append(db, project, f"files:{session}:{message}", "file-change", {
            "sessionId": session, "files": validated, "createdAt": datetime.now(timezone.utc).isoformat(),
        })
    return {"ok": True}


def _file_changes(project, session=None, *, before=None, after=None, limit=30):
    order = "ASC" if after is not None else "DESC"
    upper = before - 1 if before is not None else 2**63 - 1
    with closing(workspace.database()) as db:
        ensure(db)
        rows = db.execute(f"""SELECT seq,payload FROM labweave_project_flow WHERE project=? AND kind='file-change'
            AND (? IS NULL OR json_extract(payload,'$.sessionId')=?)
            AND seq>? AND seq<=? ORDER BY seq {order} LIMIT ?""",
                          (project, session, session, after or 0, upper, limit)).fetchall()
    return [{"id": seq, **json.loads(payload)} for seq, payload in rows]


def file_changes(project, session):
    return _file_changes(project, session) if session else []


def file_change_page(project, before=None, after=None):
    if before is not None and (type(before) is not int or not 0 < before <= 2**63 - 1):
        raise workspace.WorkspaceError(400, "无效的文件变更历史位置。")
    if after is not None:
        if before is not None or type(after) is not int or not 0 <= after <= 2**63 - 1:
            raise workspace.WorkspaceError(400, "无效的文件变更历史位置。")
        events = _file_changes(project, after=after, limit=31)
        return {"events": events[:30], "hasMore": len(events) > 30}
    events = _file_changes(project, before=before, limit=31)
    return {"events": events[:30], "nextBefore": events[29]["id"] if len(events) > 30 else None}


def changed_prerequisites(db, project, task_ids):
    if not task_ids:
        return []
    if not isinstance(task_ids, list) or len(task_ids) > 12 or any(
            not isinstance(identity, str) or len(identity) > 100 for identity in task_ids):
        raise workspace.WorkspaceError(400, "无效的前置任务引用。")
    return [identity for identity, in db.execute("""SELECT ids.value FROM json_each(?) AS ids
        JOIN labweave_project_flow AS completed
          ON completed.project=? AND completed.event_key=ids.value || ':completed'
        WHERE EXISTS (
          SELECT 1 FROM labweave_project_flow AS revision
          WHERE revision.project=completed.project AND revision.seq>completed.seq AND revision.kind='artifact'
            AND json_extract(revision.payload,'$.action') IN ('updated','removed')
            AND json_extract(revision.payload,'$.recordId')=json_extract(completed.payload,'$.recordId')
        )""", (json.dumps(task_ids), project))]


def retract(project, data):
    session = workspace.text(data.get("sessionId"), 160)
    marker = workspace.text(data.get("messageId"), 200)
    messages = data.get("messageIds")
    if not session or not marker or not isinstance(messages, list) or any(
            not isinstance(message, str) or not 0 < len(message) <= 200 for message in messages):
        raise workspace.WorkspaceError(400, "需要有效的撤回来源。")
    withdrawn = []
    with closing(workspace.database()) as db, db:
        ensure(db)
        for message in dict.fromkeys(messages):
            change = db.execute("""UPDATE labweave_project_flow SET kind='withdrawn',payload='{}'
                WHERE project=? AND event_key=? AND kind='input'""",
                                (project, f"input:{session}:{message}"))
            if change.rowcount:
                withdrawn.append(message)
        if withdrawn:
            append(db, project, f"retraction:{session}:{marker}", "retraction",
                   {"sessionId": session, "messageIds": withdrawn})
    return {"ok": True}


def read(project, after=None, audience=None):
    if after is not None and (type(after) is not int or not 0 <= after < 2**63):
        raise workspace.WorkspaceError(400, "项目事件位置无效。")
    filters = "project=? AND kind NOT IN ('withdrawn','file-change','paper-activity')"
    params = [project]
    if audience and audience.get("artifactsOnly") is True:
        filters += " AND kind IN ('decision','artifact')"
    elif audience:
        session = audience.get("sessionId", "")
        parent = audience.get("parentSessionId") or session
        work = audience.get("taskIds") or []
        scope = """kind='decision'
            OR (kind='retraction' AND json_extract(payload,'$.sessionId') IN (?,?))
            OR (kind='input' AND (json_extract(payload,'$.sessionId')=?
                OR (json_extract(payload,'$.sessionId')=? AND json_extract(payload,'$.messageId')=?)))"""
        params.extend((session, parent, session, parent, audience.get("requestId")))
        if audience.get("requestId"):
            scope += " OR (kind='coordination' AND json_extract(payload,'$.sessionId')=? AND json_extract(payload,'$.requestId')=?)"
            params.extend((parent, audience["requestId"]))
        if audience.get("includeArtifacts") is True:
            scope += " OR kind='artifact'"
        if work:
            scope += " OR (kind IN ('task','progress') AND json_extract(payload,'$.taskId') IN (" + ",".join("?" for _ in work) + "))"
            params.extend(work)
        else:
            scope += " OR (kind IN ('task','progress') AND json_extract(payload,'$.botId')=?)"
            params.append(audience.get("botId"))
        filters += " AND (" + scope + ")"
    with closing(workspace.database()) as db:
        ensure(db)
        if after is not None:
            filters += " AND seq>?"
            params.append(after)
        order = "DESC" if after is None else "ASC"
        rows = db.execute(f"""SELECT seq,kind,payload FROM labweave_project_flow
            WHERE {filters} ORDER BY seq {order} LIMIT 12""", params).fetchall()
        if after is None:
            rows.reverse()
        version = current_version(db, project)
        dependencies = changed_prerequisites(db, project, (audience or {}).get("dependencyTaskIds"))
    return {"events": [{"seq": seq, "kind": kind, **json.loads(payload)} for seq, kind, payload in rows],
            "cursor": rows[-1][0] if rows else after or 0, "version": version,
            **({"changedDependencies": dependencies} if dependencies else {})}
