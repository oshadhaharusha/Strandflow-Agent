import json
import os
import sqlite3
from datetime import datetime
from typing import List
import boto3
from botocore.exceptions import ClientError
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from strands import Agent, tool

# Set STORAGE_BACKEND="dynamodb" to use Amazon DynamoDB, or leave default "sqlite"
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "sqlite").lower()
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
SQLITE_DB_PATH = "strandflow.db"
DYNAMO_TABLE_NAME = "StrandFlowMemory"
USER_ID = "default_builder"

DEFAULT_STATE = {
    "user_profile": {
        "levels": {
            "programming_and_cloud": "Intermediate-Advanced (Python, FastAPI, Java, AWS)",
            "mathematics": "Undergraduate (Pure & Applied Math, Algorithms)",
            "creative_media": "Advanced (Photography, Video Editing, Graphic Design)"
        }
    },
    "projects": [
        {
            "id": 1,
            "title": "StrandFlow Hackathon Prototype",
            "category": "Software",
            "status": "In Progress",
            "priority": 5,
            "next_step": "Test SQLite/DynamoDB persistence and record demo video",
            "steps": [
                "Build FastAPI + Strands agent backend",
                "Upgrade persistent memory to SQLite and Amazon DynamoDB",
                "Test SQLite/DynamoDB persistence and record demo video"
            ],
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
    ],
    "study_goals": []
}

# ==========================================================
# DUAL STORAGE ENGINE: SQLITE (LOCAL) & DYNAMODB (AWS CLOUD)
# ==========================================================

def init_sqlite():
    conn = sqlite3.connect(SQLITE_DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_memory (
            user_id TEXT PRIMARY KEY,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    c.execute("SELECT state_json FROM user_memory WHERE user_id = ?", (USER_ID,))
    if not c.fetchone():
        c.execute(
            "INSERT INTO user_memory (user_id, state_json, updated_at) VALUES (?, ?, ?)",
            (USER_ID, json.dumps(DEFAULT_STATE), datetime.now().isoformat())
        )
    conn.commit()
    conn.close()

def get_dynamo_table():
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    try:
        table = dynamodb.Table(DYNAMO_TABLE_NAME)  # type: ignore[attr-defined]
        table.load()
        return table
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            table = dynamodb.create_table(  # type: ignore[attr-defined]
                TableName=DYNAMO_TABLE_NAME,
                KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "user_id", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST"
            )
            table.wait_until_exists()
            table.put_item(Item={
                "user_id": USER_ID,
                "state_json": json.dumps(DEFAULT_STATE),
                "updated_at": datetime.now().isoformat()
            })
            return table
        raise e

if STORAGE_BACKEND == "sqlite":
    init_sqlite()

def load_memory() -> dict:
    if STORAGE_BACKEND == "dynamodb":
        table = get_dynamo_table()
        res = table.get_item(Key={"user_id": USER_ID})
        if "Item" in res:
            return json.loads(res["Item"]["state_json"])
        save_memory(DEFAULT_STATE)
        return DEFAULT_STATE
    else:
        conn = sqlite3.connect(SQLITE_DB_PATH)
        c = conn.cursor()
        c.execute("SELECT state_json FROM user_memory WHERE user_id = ?", (USER_ID,))
        row = c.fetchone()
        conn.close()
        return json.loads(row[0]) if row else DEFAULT_STATE

def save_memory(data: dict):
    now = datetime.now().isoformat()
    payload = json.dumps(data)
    if STORAGE_BACKEND == "dynamodb":
        table = get_dynamo_table()
        table.put_item(Item={"user_id": USER_ID, "state_json": payload, "updated_at": now})
    else:
        conn = sqlite3.connect(SQLITE_DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO user_memory (user_id, state_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at
        """, (USER_ID, payload, now))
        conn.commit()
        conn.close()


# ==========================================================
# STRANDS DECIDER TOOLS
# ==========================================================

@tool
def capture_and_break_down_idea(
    title: str,
    category: str,
    steps: List[str],
    priority: int = 3
) -> str:
    """
    Saves a new software, video, photography, design, or university project idea
    into persistent database storage (SQLite/DynamoDB) with practical execution steps.
    
    Args:
        title: Name of the project or creative idea.
        category: One of 'Software', 'University', 'Video', 'Photography', or 'Design'.
        steps: A list of 3 to 5 concrete, actionable steps to turn the idea into reality.
        priority: Importance score from 1 (low) to 5 (critical).
    """
    mem = load_memory()
    new_id = len(mem["projects"]) + 1
    project = {
        "id": new_id,
        "title": title,
        "category": category,
        "status": "Active",
        "priority": priority,
        "next_step": steps[0] if steps else "Define first milestone",
        "steps": steps,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
    }
    mem["projects"].append(project)
    save_memory(mem)
    return f"[{STORAGE_BACKEND.upper()}] Saved '{title}' ({category}) with {len(steps)} steps. Immediate next action: {project['next_step']}"


@tool
def build_learning_roadmap(
    subject: str,
    current_level: str,
    target_goal: str,
    milestones: List[str]
) -> str:
    """
    Creates and stores a personalized study plan for a programming, math, or university topic.
    
    Args:
        subject: The technical or academic subject to learn.
        current_level: The user's current comfort level (e.g., Beginner, Intermediate, Advanced).
        target_goal: What the user wants to achieve or understand.
        milestones: Ordered list of study modules or practice exercises.
    """
    mem = load_memory()
    plan = {
        "subject": subject,
        "current_level": current_level,
        "target_goal": target_goal,
        "milestones": milestones,
        "current_focus": milestones[0] if milestones else "Review fundamentals"
    }
    mem["study_goals"].append(plan)
    save_memory(mem)
    return f"[{STORAGE_BACKEND.upper()}] Created study plan for '{subject}' ({current_level}). First milestone: {plan['current_focus']}"


@tool
def decide_next_focus(available_minutes: int = 60, energy_level: str = "medium") -> str:
    """
    The Decider Engine: Analyzes all ongoing projects and study goals in persistent memory,
    weighing priority, energy level, and available time to recommend the single best task right now.
    
    Args:
        available_minutes: How many minutes the user currently has available.
        energy_level: User's mental energy ('low', 'medium', 'high').
    """
    mem = load_memory()
    active_projects = [p for p in mem["projects"] if p["status"] != "Completed"]
    study_goals = mem["study_goals"]

    if not active_projects and not study_goals:
        return "No active projects or study plans in database. Share an idea or study topic to get started!"

    sorted_projects = sorted(active_projects, key=lambda x: x.get("priority", 3), reverse=True)
    summary = {
        "storage_engine": STORAGE_BACKEND,
        "available_minutes": available_minutes,
        "energy_level": energy_level,
        "top_projects": sorted_projects[:3],
        "active_study_goals": study_goals[:2]
    }
    return f"DECIDER_CONTEXT: {json.dumps(summary)}"


@tool
def complete_current_step(project_title: str) -> str:
    """
    Marks the current 'next_step' of a project as done and advances to the following step.
    
    Args:
        project_title: The title (or partial title) of the project to update.
    """
    mem = load_memory()
    for proj in mem["projects"]:
        if project_title.lower() in proj["title"].lower():
            steps = proj.get("steps", [])
            curr = proj.get("next_step", "")
            if curr in steps:
                idx = steps.index(curr)
                if idx + 1 < len(steps):
                    proj["next_step"] = steps[idx + 1]
                    save_memory(mem)
                    return f"Awesome progress! Advanced '{proj['title']}' to next step: {proj['next_step']}"
                else:
                    proj["status"] = "Completed"
                    proj["next_step"] = "All steps completed! Shipped!"
                    save_memory(mem)
                    return f"Congratulations! You actually built and completed '{proj['title']}'!"
    return f"Could not find an active project matching '{project_title}'."


# ==========================================================
# AGENT & FASTAPI SERVER
# ==========================================================

SYSTEM_PROMPT = """You are StrandFlow, a Personal Learning, Creative, and Project Decider Agent.
The user talks to you whenever they have an idea, a technical question, a university subject to master, or a creative project (video, photography, design, software).

Your core behaviors:
1. EXPLAIN AT LEVEL: Calibrate programming, CS, and math explanations to the user's background.
2. CAPTURE & BREAK DOWN: Whenever the user shares a new idea for a video, photo shoot, design, or software build, call `capture_and_break_down_idea` to turn it into 3-5 practical steps.
3. STUDY PLANS: When the user wants to learn a topic or prep for an exam, call `build_learning_roadmap`.
4. THE DECIDER: When the user asks what to focus on next, call `decide_next_focus` and give ONE decisive recommendation that moves them from 'I have an idea' to 'I actually built it'.
Keep responses conversational, punchy, and natural for voice output."""

agent = Agent(
    system_prompt=SYSTEM_PROMPT,
    tools=[
        capture_and_break_down_idea,
        build_learning_roadmap,
        decide_next_focus,
        complete_current_step
    ]
)

app = FastAPI(title="StrandFlow Decider Agent")

class ChatRequest(BaseModel):
    message: str

@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    response = agent(req.message)
    return {"reply": str(response), "memory": load_memory()}

@app.get("/api/memory")
async def get_memory_endpoint():
    return load_memory()

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()