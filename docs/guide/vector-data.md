# Vector data

The **Vector data** view shows what a SQLite or PostgreSQL database holds for vector search: its vector columns and
their types, the vector indexes and their state, the embedding dimensions, the embedding models its metadata names,
how many embeddings are stored, and whether pgvector or sqlite-vec is installed. It only reads. Studio never embeds
text, builds or rebuilds an index, installs an extension or changes anything.

## Inspecting a database

1. Choose the **Connection profile**.
2. Select **Inspect**. Studio opens the profile's read-only connection (a PROD profile asks "Production database"
   first, as everywhere) and reads the catalog and the metadata tables.
3. **Inspect again** reads the shown connection again; it never opens a new one.

## What the view shows

**Extension.** For PostgreSQL, pgvector's state as the server reports it: installed (with its version and schema),
offered by the server but not installed, not on the server at all, or installed in a schema outside the connection's
search path. When the server offers a newer version, the view says so. For SQLite, the status of sqlite-vec is
**not determinable**: sqlite-vec is loaded by the application into its own process and is not part of the database
file, and Studio loads no extension. The view shows what the file holds instead, and for each `vec0` table the
sqlite-vec version that created it, when the table records it.

**Vector columns.** Each column with its type, its dimensions, the stored embeddings and its indexes (method, state,
operator class, options and size). Studio finds:

- PostgreSQL columns of pgvector's types `vector`, `halfvec` and `sparsevec` (also through a domain), in every schema
  the role can see;
- sqlite-vec `vec0` virtual tables, read from their definition, without the module;
- the vector tables of Orkexa (marked **Orkexa layout**): on PostgreSQL `<prefix>vector_spaces` with
  `<prefix>chunk_vectors` (`real[]` vectors), on SQLite `index_spaces` with `index_vectors` (BLOB vectors) and the
  older `index_vec` table. A float array or a BLOB column is listed only when such metadata confirms that it holds
  vectors.

**Search.** A column shows **ANN index (hnsw)** (or `ivfflat`, `diskann`) only when a valid index of an
approximate-nearest-neighbor method is on it. Every other column shows **Exact search (no ANN index)**: searches on it
compare every row. That includes a `vector` column without an index, one whose only ANN index is invalid (for example
after a failed `CREATE INDEX CONCURRENTLY`; the index is listed as "invalid, not used"), Orkexa's `real[]` and BLOB
vectors, and sqlite-vec `vec0` tables, which sqlite-vec searches by a full scan.

An index marked **on an expression** (for example on `embedding::vector(384)`) is used only by queries that use the
same expression, and a **partial** index only for the rows its condition covers. The operator class shown next to an
index decides which distance it serves: an index with `vector_cosine_ops` speeds up cosine-distance queries, not L2.

**Stored embeddings.** Studio counts at most 100,000 rows per table and never longer than 10 seconds (or the
connection's time limit, if shorter); past that the view says "more than 100,000". For a PostgreSQL table that the
statistics put above 100,000 rows, Studio does not count at all and shows "about N rows (estimate)" from the
statistics. A count that was stopped shows "unknown". With row-level security, a count is the rows your role sees.

**Embedding models.** For Orkexa's vector spaces: the space, the tenant and namespace, the model and its provider,
the model build (digest), whether the vectors are normalized, the dimensions, the state (`active`, or `building`
while a reindex runs) and the number of embeddings. Other databases have no standard place for a model name, so the
view says that the metadata names none.

## Hints

Below the tables, the view explains what it found, for example:

- pgvector is not installed (and how an administrator installs it; Studio never does);
- a column declares no dimensions, so it cannot have an HNSW or IVFFlat index;
- an index is invalid and not used;
- your role may not read a table, so its embeddings are not counted or its models not shown;
- the identity of a vector space is incomplete, or its dimensions do not match;
- a `vec0` table needs sqlite-vec loaded in the application to be queried.

An empty database, or one without vector data, shows "No vector data found".
