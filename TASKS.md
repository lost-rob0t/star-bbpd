# TASKS.md

Order of work (one per iteration):

1) Stabilize publisher API (publish_* or tell(Publish*)) and ban internals access from ToolActor.
2) Eliminate shared publish channel usage; publishing must use per-worker connections only.
3) Delete or hard-deprecate legacy publisher path so it cannot be wired accidentally.
4) Make main wiring consistent (publisher proxy everywhere; pool instance only for shutdown).
5) Add fail-fast asserts that detect unsafe publisher/rabbit wiring at startup.
