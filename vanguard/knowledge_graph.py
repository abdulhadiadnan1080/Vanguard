"""Vulnerability knowledge graph: the memory of the flywheel.

Nodes: Model, Weakness, Signal, Candidate, Task, Trial. Edges are typed, and outcome edges carry evidence counts, so the
graph can say which weaknesses and signals defeat which model and how often that was confirmed.

    (Candidate) -[HAS_SIGNAL]-> (Signal)                 finder factors and weakness detect rules that fired
    (Task) -[BUILT_FROM]-> (Candidate)
    (Task) -[TARGETS]-> (Weakness)                        curated tags for controlled experiments (catalog.json)
    (Task) -[MITIGATED_BY]-> (Signal)                     report hint, visible failing test, ...
    (Trial) -[ON]-> (Task), (Trial) -[BY]-> (Model)        oracle/nop trials use VALIDATES instead of ON
    (Task|Weakness|Signal) -[DEFEATS {fails, runs, tasks_failed, tasks}]-> (Model)   recomputed from valid trials
    (Candidate) -[PREDICTED {p_fail, version, at}]-> (Model)                         written by `vanguard.py rank`

Stored in SQLite (standard library only) so every stage of the loop can read and write it.
"""
import datetime
import json
import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY, type TEXT NOT NULL, label TEXT, props TEXT NOT NULL DEFAULT '{}', updated TEXT);
CREATE INDEX IF NOT EXISTS nodes_type ON nodes(type);
CREATE TABLE IF NOT EXISTS edges (
    src TEXT NOT NULL, rel TEXT NOT NULL, dst TEXT NOT NULL, props TEXT NOT NULL DEFAULT '{}', updated TEXT,
    PRIMARY KEY (src, rel, dst));
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst, rel);
CREATE TABLE IF NOT EXISTS metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL, props TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS predictions (
    candidate TEXT NOT NULL, model TEXT NOT NULL, p_fail REAL NOT NULL, version TEXT NOT NULL, at TEXT NOT NULL,
    PRIMARY KEY (candidate, model, version));
"""


def now():
    """UTC, the same clock Harbor uses for started_at, so predictions and runs can be ordered."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def parse_ts(s):
    """ISO timestamp (Harbor writes UTC with 'Z' or an offset) -> aware datetime, or None."""
    if not s:
        return None
    try:
        t = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


class Graph:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        # several commands may run at once (watch + validate + loop): WAL lets readers proceed during a write,
        # and the timeout makes a writer wait for the lock instead of failing with "database is locked"
        self.db = sqlite3.connect(path, timeout=120)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=120000")
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.commit()
        self.db.close()

    # ------------------------------------------------------------------ writes
    def node(self, id, type, label=None, **props):
        """Upsert a node. New props are merged into the stored ones; None values are ignored."""
        row = self.db.execute("SELECT props FROM nodes WHERE id=?", (id,)).fetchone()
        merged = json.loads(row[0]) if row else {}
        merged.update({k: v for k, v in props.items() if v is not None})
        self.db.execute(
            "INSERT INTO nodes(id, type, label, props, updated) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET type=excluded.type, label=COALESCE(excluded.label, nodes.label), "
            "props=excluded.props, updated=excluded.updated",
            (id, type, label, json.dumps(merged, sort_keys=True), now()))
        return id

    def edge(self, src, rel, dst, **props):
        """Upsert an edge. Its props are replaced, so recomputed edges (DEFEATS) never keep stale counts."""
        self.db.execute(
            "INSERT INTO edges(src, rel, dst, props, updated) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(src, rel, dst) DO UPDATE SET props=excluded.props, updated=excluded.updated",
            (src, rel, dst, json.dumps(props, sort_keys=True), now()))

    def drop_edges(self, rel, src_prefix=""):
        self.db.execute("DELETE FROM edges WHERE rel=? AND src LIKE ?", (rel, src_prefix + "%"))

    def metric(self, kind, **props):
        self.db.execute("INSERT INTO metrics(at, kind, props) VALUES (?, ?, ?)", (now(), kind, json.dumps(props, sort_keys=True)))

    def record_prediction(self, candidate, model, p_fail, version, at=None):
        """Append-only history: the PREDICTED edge holds the latest score, this table keeps every one, so a
        prediction made before a task was built can later be checked against its real outcome."""
        self.db.execute("INSERT OR IGNORE INTO predictions(candidate, model, p_fail, version, at) VALUES (?, ?, ?, ?, ?)",
                        (candidate, model, p_fail, version, at or now()))

    def prediction_before(self, candidate, model, before):
        """Latest prediction for (candidate, model) made strictly before the aware datetime `before`."""
        best = None
        for p, v, at in self.db.execute("SELECT p_fail, version, at FROM predictions WHERE candidate=? AND model=?",
                                        (candidate, model)):
            t = parse_ts(at)
            if t and t < before and (best is None or t > best[2]):
                best = (p, v, t)
        return best

    # ------------------------------------------------------------------ reads
    def get(self, id):
        row = self.db.execute("SELECT id, type, label, props FROM nodes WHERE id=?", (id,)).fetchone()
        return _node(row) if row else None

    def nodes(self, type=None):
        q, a = ("SELECT id, type, label, props FROM nodes WHERE type=? ORDER BY id", (type,)) if type else \
               ("SELECT id, type, label, props FROM nodes ORDER BY id", ())
        return [_node(r) for r in self.db.execute(q, a)]

    def edges(self, src=None, rel=None, dst=None):
        where, args = [], []
        for col, val in (("src", src), ("rel", rel), ("dst", dst)):
            if val is not None:
                where.append(col + "=?")
                args.append(val)
        q = "SELECT src, rel, dst, props FROM edges" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY src, dst"
        return [{"src": s, "rel": r, "dst": d, **json.loads(p)} for s, r, d, p in self.db.execute(q, args)]

    def metrics(self, kind):
        return [{"at": at, **json.loads(p)} for at, p in
                self.db.execute("SELECT at, props FROM metrics WHERE kind=? ORDER BY id", (kind,))]

    def counts(self):
        nodes = dict(self.db.execute("SELECT type, COUNT(*) FROM nodes GROUP BY type"))
        edges = dict(self.db.execute("SELECT rel, COUNT(*) FROM edges GROUP BY rel"))
        return nodes, edges

    def export_json(self, path):
        data = {"nodes": self.nodes(), "edges": self.edges(), "metrics": {
            k: self.metrics(k) for (k,) in self.db.execute("SELECT DISTINCT kind FROM metrics")}}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        return len(data["nodes"]), len(data["edges"])


def _node(row):
    id, type, label, props = row
    return {"id": id, "type": type, "label": label, **json.loads(props)}
