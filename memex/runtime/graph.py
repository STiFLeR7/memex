"""Atomic immutable structural views through Graphiti's real Neo4j sessions."""
from dataclasses import asdict
import asyncio


class StructuralGraphStore:
    def __init__(self, driver):
        self.driver = driver
        self._schema_lock = asyncio.Lock()
        self._schema_ready = False

    async def ensure_schema(self):
        async with self._schema_lock:
            if self._schema_ready:
                return
            identities = {
                "worktree": ("MemexWorktree", "n.repo_id,n.worktree_id"),
                "view": ("MemexView", "n.view_id"),
                "file": ("MemexFile", "n.view_id,n.path"),
                "symbol": ("MemexSymbol", "n.view_id,n.file,n.qualified_name"),
                "call": ("MemexCall", "n.view_id,n.file,n.caller,n.callee,n.line"),
                "import": ("MemexImport", "n.view_id,n.module"),
            }
            for name, (label, fields) in identities.items():
                await self.driver.execute_query(
                    f"CREATE CONSTRAINT memex_{name}_identity IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE ({fields}) IS UNIQUE",
                )
            self._schema_ready = True

    async def completed(self, view_id: str) -> bool:
        result = await self.driver.execute_query(
            "MATCH (v:MemexView {view_id:$view}) RETURN v.completed AS completed", params={"view": view_id},
        )
        return bool(result.records and result.records[0]["completed"])

    async def coverage_complete(self, view_id: str) -> bool:
        result = await self.driver.execute_query(
            "MATCH (v:MemexView {view_id:$view}) RETURN v.coverage_complete AS complete", params={"view": view_id},
        )
        return bool(result.records and result.records[0]["complete"])

    async def published_view(self, repo_id: str, worktree_id: str) -> str | None:
        result = await self.driver.execute_query(
            "MATCH (w:MemexWorktree {repo_id:$repo,worktree_id:$worktree}) RETURN w.view_id AS view",
            params={"repo": repo_id, "worktree": worktree_id},
        )
        return result.records[0]["view"] if result.records else None

    async def publish(self, view, contributions, *, fail_before_complete=False) -> bool:
        await self.ensure_schema()
        files = [asdict(item) for item in contributions]
        params = dict(view=view.view_id, repo=view.repo_id, worktree=view.worktree_id,
                      generation=view.content_generation, manifest=view.manifest_hash,
                      head=view.head_commit, complete=all(item.coverage == "complete" for item in contributions))

        async def write(transaction):
            locked = await transaction.run(
                "MERGE (w:MemexWorktree {repo_id:$repo,worktree_id:$worktree}) "
                "SET w.lock_epoch=coalesce(w.lock_epoch,0)+1 RETURN coalesce(w.generation,0) AS generation", **params,
            )
            row = await locked.single()
            if row["generation"] > view.content_generation:
                return False
            existing = await transaction.run(
                "MATCH (v:MemexView {view_id:$view}) RETURN v.completed AS complete", **params,
            )
            existing_row = await existing.single()
            if not existing_row or not existing_row["complete"]:
                await transaction.run(
                    "MERGE (v:MemexView {view_id:$view}) SET v.repo_id=$repo, v.worktree_id=$worktree, "
                    "v.generation=$generation, v.manifest_hash=$manifest, v.head_commit=$head", **params,
                )
                for file in files:
                    file_params = params | {"file": file["path"], "hash": file["content_hash"], "coverage": file["coverage"]}
                    await transaction.run(
                        "MATCH (v:MemexView {view_id:$view}) "
                        "MERGE (f:MemexFile {view_id:$view,path:$file}) "
                        "SET f.content_hash=$hash,f.coverage=$coverage MERGE (v)-[:HAS_FILE]->(f)", **file_params,
                    )
                    for symbol in file["symbols"]:
                        await transaction.run(
                            "MATCH (f:MemexFile {view_id:$view,path:$file}) "
                            "MERGE (s:MemexSymbol {view_id:$view,file:$file,qualified_name:$qualified_name}) "
                            "SET s.name=$name,s.kind=$kind,s.line=$line,s.signature=$signature "
                            "MERGE (f)-[:HAS_SYMBOL]->(s)", **(file_params | symbol),
                        )
                    for call in file["calls"]:
                        await transaction.run(
                            "MATCH (f:MemexFile {view_id:$view,path:$file}) "
                            "MERGE (c:MemexCall {view_id:$view,file:$file,caller:$caller,callee:$callee,line:$line}) "
                            "SET c.resolution='unresolved' MERGE (f)-[:HAS_CALL]->(c)", **(file_params | call),
                        )
                    for module in file["imports"]:
                        await transaction.run(
                            "MATCH (f:MemexFile {view_id:$view,path:$file}) "
                            "MERGE (i:MemexImport {view_id:$view,module:$module}) MERGE (f)-[:IMPORTS]->(i)",
                            **(file_params | {"module": module}),
                        )
                # Syntactic names are evidence, not a proven Python binding.
                # A unique bare name can still refer to an imported attribute.
            if fail_before_complete:
                raise RuntimeError("injected transaction failure")
            await transaction.run(
                "MATCH (v:MemexView {view_id:$view}), (w:MemexWorktree {repo_id:$repo,worktree_id:$worktree}) "
                "SET v.completed=true,v.coverage_complete=$complete,w.view_id=$view,w.generation=$generation", **params,
            )
            return True

        async with self.driver.session() as session:
            return await session.execute_write(write)
