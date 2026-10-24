"""RISK-003 spike: LangGraph interrupt + AsyncSqliteSaver survives a hard process kill.

Usage (each phase runs in a separate OS process):
    python risk003_interrupt_restart.py start   # runs until the approval interrupt, then idles (to be killed)
    python risk003_interrupt_restart.py resume  # new process: resume with an approval decision
    python risk003_interrupt_restart.py report  # print node-execution counters and outbox rows

No network access and no LLM calls.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import TypedDict

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

os.environ["LANGSMITH_TRACING"] = "false"

VAR = Path(__file__).parent / "var"
VAR.mkdir(exist_ok=True)
CKPT_DB = VAR / "risk003_checkpoints.db"
APP_DB = VAR / "risk003_app.db"
THREAD = {"configurable": {"thread_id": "trip-1:rev-1"}}


def app_db() -> sqlite3.Connection:
    conn = sqlite3.connect(APP_DB)
    conn.execute("CREATE TABLE IF NOT EXISTS node_runs(node TEXT, pid INTEGER, ts REAL)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS outbox(trip_id TEXT, revision INTEGER, kind TEXT, pid INTEGER,"
        " UNIQUE(trip_id, revision, kind))"
    )
    return conn


def record(node: str) -> None:
    with app_db() as conn:
        conn.execute("INSERT INTO node_runs VALUES (?,?,?)", (node, os.getpid(), time.time()))


class TripState(TypedDict, total=False):
    trip_id: str
    revision: int
    total_fen: int
    decision: str
    status: str


def plan(state: TripState) -> TripState:
    record("plan")
    return {"total_fen": 476000, "status": "PLANNED"}


def approval(state: TripState) -> TripState:
    record("approval_enter")  # LangGraph re-executes this node on resume; must stay side-effect free
    decision = interrupt({"trip_id": state["trip_id"], "revision": state["revision"], "total_fen": state["total_fen"]})
    record("approval_after_resume")
    return {"decision": decision["decision"], "status": "APPROVED" if decision["decision"] == "approve" else "REJECTED"}


def deliver(state: TripState) -> TripState:
    record("deliver")
    with app_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO outbox VALUES (?,?,?,?)",
            (state["trip_id"], state["revision"], "trip_delivery", os.getpid()),
        )
    return {"status": "DELIVERED"}


def route(state: TripState) -> str:
    return "deliver" if state.get("decision") == "approve" else END


def build():
    g = StateGraph(TripState)
    g.add_node("plan", plan)
    g.add_node("approval", approval)
    g.add_node("deliver", deliver)
    g.add_edge(START, "plan")
    g.add_edge("plan", "approval")
    g.add_conditional_edges("approval", route, ["deliver", END])
    g.add_edge("deliver", END)
    return g


async def with_graph(fn):
    async with aiosqlite.connect(CKPT_DB) as conn:
        saver = AsyncSqliteSaver(conn)
        graph = build().compile(checkpointer=saver)
        return await fn(graph)


async def start():
    async def run(graph):
        result = await graph.ainvoke({"trip_id": "trip-1", "revision": 1}, THREAD)
        snap = await graph.aget_state(THREAD)
        print(json.dumps({
            "pid": os.getpid(),
            "interrupts": [i.value for i in result.get("__interrupt__", [])],
            "next": list(snap.next),
        }, ensure_ascii=False))
        print("WAITING_FOR_KILL", flush=True)
        await asyncio.sleep(120)
    await with_graph(run)


async def resume():
    async def run(graph):
        before = await graph.aget_state(THREAD)
        result = await graph.ainvoke(Command(resume={"decision": "approve"}), THREAD)
        after = await graph.aget_state(THREAD)
        print(json.dumps({
            "pid": os.getpid(),
            "next_before_resume": list(before.next),
            "pending_interrupt_before_resume": [t.interrupts[0].value for t in before.tasks if t.interrupts],
            "status_after": result.get("status"),
            "next_after": list(after.next),
        }, ensure_ascii=False))
    await with_graph(run)


async def resume_again():
    """A duplicate resume after completion must not re-run deliver or duplicate the outbox row."""
    async def run(graph):
        snap = await graph.aget_state(THREAD)
        print(json.dumps({"pid": os.getpid(), "next": list(snap.next), "status": snap.values.get("status")}))
        if snap.next:  # only resume when the graph is actually paused
            await graph.ainvoke(Command(resume={"decision": "approve"}), THREAD)
        else:
            print("GRAPH_ALREADY_COMPLETE: duplicate decision must be rejected at API layer (409)")
    await with_graph(run)


def report():
    with app_db() as conn:
        runs = conn.execute("SELECT node, COUNT(*), COUNT(DISTINCT pid) FROM node_runs GROUP BY node").fetchall()
        outbox = conn.execute("SELECT trip_id, revision, kind, pid FROM outbox").fetchall()
    print(json.dumps({"node_runs": {n: {"count": c, "distinct_pids": p} for n, c, p in runs},
                      "outbox_rows": outbox}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    phase = sys.argv[1]
    if phase == "reset":
        for p in VAR.glob("risk003_*"):
            p.unlink()
    elif phase == "report":
        report()
    else:
        asyncio.run({"start": start, "resume": resume, "resume_again": resume_again}[phase]())
