"""
AI Notes — Day 17 — one-off embedding backfill.

Search backfills missing/stale embeddings automatically and inline
(capped per request — see MAX_INLINE_EMBED_BACKFILL in notes.py), so
you don't strictly need this script. It's here for convenience if you
want every note embedded up front — e.g. right after installing
sentence-transformers for the first time, or after importing a batch
of old notes — rather than waiting for search requests to catch up a
few dozen notes at a time.

Usage (from the backend/ directory, with your .env configured):

    python backfill_embeddings.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv

load_dotenv()

from app import create_app
from db import get_db
from embeddings import embed_texts, embeddings_status, encode_json
from notes import text_hash


def main():
    app = create_app()
    with app.app_context():
        available, reason = embeddings_status()
        if not available:
            print(f"Semantic search embeddings aren't available: {reason}")
            print("Install sentence-transformers (see requirements.txt) and try again.")
            return

        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT id, title, text, embedding, embedding_source_hash FROM notes")
            rows = cur.fetchall()

        stale = [
            r for r in rows if not r["embedding"] or r["embedding_source_hash"] != text_hash(r["text"])
        ]
        print(f"{len(rows)} notes total, {len(stale)} need a new embedding.")

        if not stale:
            print("Nothing to do.")
            return

        BATCH = 32
        done = 0
        for i in range(0, len(stale), BATCH):
            batch = stale[i : i + BATCH]
            texts = [f"{r['title']}\n{r['text']}".strip() for r in batch]
            vecs = embed_texts(texts)
            with db.cursor() as cur:
                for row, vec in zip(batch, vecs):
                    if vec is None:
                        continue
                    cur.execute(
                        """UPDATE notes SET embedding = %s, embedding_source_hash = %s,
                           embedding_generated_at = now() WHERE id = %s""",
                        (encode_json(vec), text_hash(row["text"]), row["id"]),
                    )
                    done += 1
            db.commit()
            print(f"  embedded {min(i + BATCH, len(stale))}/{len(stale)}...")

        print(f"Done. {done} notes embedded.")


if __name__ == "__main__":
    main()
