"""Full-text search over stored situations (FTS5), when this SQLite build has it. Without FTS5, history works and
the `q` filter answers 400 search_unavailable."""


def run(c, database):
    if not c.execute("SELECT sqlite_compileoption_used('ENABLE_FTS5')").fetchone()[0]:
        return
    c.execute("CREATE VIRTUAL TABLE decision_fts USING fts5(text, tokenize = 'unicode61 remove_diacritics 2')")
    c.execute("CREATE TRIGGER decisions_fts_delete AFTER DELETE ON decisions BEGIN DELETE FROM decision_fts WHERE rowid = OLD.seq; END")
    c.execute("INSERT INTO decision_fts (rowid, text) SELECT d.seq, b.rendered_state FROM decisions d "
              "JOIN decision_bodies b ON b.decision_seq = d.seq WHERE d.storage = 'full' AND b.rendered_state IS NOT NULL")
