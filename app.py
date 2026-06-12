import autogen
from rich import print
import chainlit as cl
import uuid
from typing import Annotated
from chainlit.input_widget import (
   Select, Slider, Switch)
from autogen import AssistantAgent
from src.agents import ChainlitUserProxyAgent, ChainlitAssistantAgent
from graphrag.query.cli import run_global_search, run_local_search
from src.adapters.db import DatabaseManager

import requests
import ollama

def get_optimal_models() -> tuple[str, str]:
    try:
        res = requests.get("http://localhost:11434/api/tags", timeout=2)
        if res.status_code == 200:
            models = [m["name"].split(":")[0] for m in res.json().get("models", [])]
            
            # Prefer llama3 or mistral for heavy reasoning
            heavy = "llama3"
            for h in ["llama3", "mistral", "llama3.1"]:
                if h in models:
                    heavy = h
                    break
                    
            # Prefer gemma, phi3, or qwen for fast chat
            fast = heavy
            for f in ["phi3", "gemma", "gemma2", "qwen", "qwen2"]:
                if f in models:
                    fast = f
                    break
            return heavy, fast
    except Exception:
        pass
    return "llama3", "llama3"

HEAVY_MODEL, FAST_MODEL = get_optimal_models()
print(f"Optimal Models Detected - Heavy: {HEAVY_MODEL}, Fast: {FAST_MODEL}")

# Ollama LLM Configs for Heavy and Fast models
llm_config_heavy = {
    "seed": 42,
    "temperature": 0,
    "config_list": [
        {
            "model": HEAVY_MODEL,
            "base_url": "http://localhost:11434/v1",
            "api_key": "ollama",
        }
    ],
    "timeout": 60000,
}

llm_config_fast = {
    "seed": 42,
    "temperature": 0.1,
    "config_list": [
        {
            "model": FAST_MODEL,
            "base_url": "http://localhost:11434/v1",
            "api_key": "ollama",
        }
    ],
    "timeout": 30000,
}

async def detect_query_intent(query: str) -> str:
    """Classifies user prompt intent to bypass expensive GraphRAG indexing calls for chit-chat."""
    try:
        response = await ollama.AsyncClient().generate(
            model=FAST_MODEL,
            system="Classify the user query as 'SEARCH' (if it asks for factual knowledge, document data, files, or specific retrieval) or 'CONVERSATIONAL' (if it is a greeting, chit-chat, or general follow-up). Output only the single word: SEARCH or CONVERSATIONAL.",
            prompt=query,
            options={"temperature": 0.0, "num_predict": 5}
        )
        intent = response.get("response", "").strip().upper()
        if "CONVERSATIONAL" in intent:
            return "CONVERSATIONAL"
        return "SEARCH"
    except Exception:
        # Robust regex-based fallback classification
        conversational_keywords = {"hi", "hello", "hey", "who are you", "what is your name", "exit", "quit", "thanks", "thank you"}
        words = set(query.lower().strip().split())
        if words.intersection(conversational_keywords):
            return "CONVERSATIONAL"
        return "SEARCH"

@cl.on_chat_start
async def on_chat_start():
  try:
    settings = await cl.ChatSettings(
            [      
                Switch(id="Search_type", label="(GraphRAG) Local Search", initial=True),       
                Select(
                    id="Gen_type",
                    label="(GraphRAG) Content Type",
                    values=["prioritized list", "single paragraph", "multiple paragraphs", "multiple-page report"],
                    initial_index=1,
                ),          
                Slider(
                    id="Community",
                    label="(GraphRAG) Community Level",
                    initial=0,
                    min=0,
                    max=2,
                    step=1,
                ),

            ]
        ).send()

    response_type = settings["Gen_type"]
    community = settings["Community"]
    local_search = settings["Search_type"]
    
    cl.user_session.set("Gen_type", response_type)
    cl.user_session.set("Community", community)
    cl.user_session.set("Search_type", local_search)

    retriever   = AssistantAgent(
       name="Retriever", 
       llm_config=llm_config_heavy, 
       system_message="""Only execute the function query_graphRAG to look for context. 
                    Output 'TERMINATE' when an answer has been provided.""",
       max_consecutive_auto_reply=1,
       human_input_mode="NEVER", 
       description="Retriever Agent"
     )

    chatter = AssistantAgent(
       name="Chatter",
       llm_config=llm_config_fast,
       system_message="""You are a helpful local assistant. Respond to greetings, small talk, and general queries directly and concisely. Do not attempt to query the graph database.""",
       description="Conversational Agent"
    )

    user_proxy = ChainlitUserProxyAgent(
        name="User_Proxy",
        human_input_mode="ALWAYS",
        llm_config=llm_config_fast,
        is_termination_msg=lambda x: x.get("content", "").rstrip().endswith("TERMINATE"),
        code_execution_config=False,
        system_message='''A human admin. Interact with the retriever or chatter to provide context.''',
        description="User Proxy Agent"
    )
    
    print("Set agents.")

    cl.user_session.set("Query Agent", user_proxy)
    cl.user_session.set("Retriever", retriever)
    cl.user_session.set("Chatter", chatter)

    # Initialize SQLite database manager
    db_mgr = DatabaseManager()
    await db_mgr.initialize()
    cl.user_session.set("db_mgr", db_mgr)

    # Check for existing sessions
    sessions = await db_mgr.list_sessions()
    session_id = None

    if sessions:
        # Ask user if they wish to resume the last active session
        actions = [
            cl.Action(name="resume_chat", value=sessions[0]["session_id"], label="🔄 Resume Last Chat"),
            cl.Action(name="new_chat", value="new", label="➕ Start New Chat")
        ]
        res = await cl.AskActionMessage(
            content="We found a previous chat session. Would you like to resume it or start a new one?",
            actions=actions
        ).send()
        
        if res and res.get("value") == "new":
            session_id = str(uuid.uuid4())
            await db_mgr.create_session(session_id, f"Session - {session_id[:8]}")
            cl.user_session.set("chat_session_id", session_id)
            msg = cl.Message(content="Hello! What task would you like to get done today?", author="User_Proxy")
            await msg.send()
        else:
            session_id = res.get("value") if res else sessions[0]["session_id"]
            cl.user_session.set("chat_session_id", session_id)
            history = await db_mgr.get_session_messages(session_id)
            
            await cl.Message(content="*Resuming previous chat history...*").send()
            for msg in history:
                await cl.Message(
                    content=msg["content"],
                    author=msg["sender"]
                ).send()
    else:
        # Start a brand new session
        session_id = str(uuid.uuid4())
        await db_mgr.create_session(session_id, f"Session - {session_id[:8]}")
        cl.user_session.set("chat_session_id", session_id)
        msg = cl.Message(content="Hello! What task would you like to get done today?", author="User_Proxy")
        await msg.send()

    print("Session ready.")
    
  except Exception as e:
    print("Error: ", e)
    pass

@cl.on_settings_update
async def setup_agent(settings):
    response_type = settings["Gen_type"]
    community = settings["Community"]
    local_search = settings["Search_type"]
    cl.user_session.set("Gen_type", response_type)
    cl.user_session.set("Community", community)
    cl.user_session.set("Search_type", local_search)
    print("on_settings_update", settings)

@cl.on_message
async def run_conversation(message: cl.Message):
    print("Running conversation")
    INPUT_DIR = None
    ROOT_DIR = '.'    
    CONTEXT = message.content
    MAX_ITER = 10   
    RESPONSE_TYPE = cl.user_session.get("Gen_type")
    COMMUNITY = cl.user_session.get("Community")
    LOCAL_SEARCH = cl.user_session.get("Search_type")

    retriever   = cl.user_session.get("Retriever")
    chatter     = cl.user_session.get("Chatter")
    user_proxy  = cl.user_session.get("Query Agent")
    db_mgr      = cl.user_session.get("db_mgr")
    session_id  = cl.user_session.get("chat_session_id")
    print("Setting groupchat")

    # Save initial user message to database
    if db_mgr and session_id:
        await db_mgr.save_message(session_id, sender="User", role="user", content=CONTEXT)

    # Classify intent to route query to correct agent group and model tier
    intent = await detect_query_intent(CONTEXT)
    print(f"Query Intent: {intent}")

    if intent == "CONVERSATIONAL":
        active_agents = [user_proxy, chatter]
        manager_config = llm_config_fast
    else:
        active_agents = [user_proxy, retriever]
        manager_config = llm_config_heavy

    def state_transition(last_speaker: autogen.Agent, groupchat: autogen.GroupChat) -> autogen.Agent | None:
        if last_speaker is user_proxy:
            return retriever if retriever in groupchat.agents else chatter
        if last_speaker in (retriever, chatter):
            return user_proxy
        return None

    async def query_graphRAG(
          question: Annotated[str, 'Query string containing information that you want from RAG search']
                          ) -> str:
        try:
            if LOCAL_SEARCH:
                print(LOCAL_SEARCH)
                result = run_local_search(INPUT_DIR, ROOT_DIR, COMMUNITY ,RESPONSE_TYPE, question)
            else:
                result = run_global_search(INPUT_DIR, ROOT_DIR, COMMUNITY ,RESPONSE_TYPE, question)
        except Exception as e:
            error_msg = f"❌ Error during GraphRAG query: {str(e)}\n\nPlease verify that your Ollama service is active and running."
            await cl.Message(content=error_msg).send()
            return "Error: Could not retrieve context."
            
        await cl.Message(content=result).send()
        return result

    autogen.register_function(
        query_graphRAG,
        caller=retriever,
        executor=user_proxy,
        name="query_graphRAG",
        description="Retrieve content for question answering from the GraphRAG knowledge base.",
    )

    groupchat = autogen.GroupChat(
        agents=active_agents,
        messages=[],
        max_round=MAX_ITER,
        speaker_selection_method=state_transition,
        allow_repeat_speaker=True,
    )
    manager = autogen.GroupChatManager(groupchat=groupchat,
                                       llm_config=manager_config, 
                                       is_termination_msg=lambda x: x.get("content", "") and x.get("content", "").rstrip().endswith("TERMINATE"),
                                       code_execution_config=False,
                                       )    

# -------------------- Conversation Logic. Edit to change your first message based on the Task you want to get done. ----------------------------- # 
    if len(groupchat.messages) == 0: 
      await cl.make_async(user_proxy.initiate_chat)( manager, message=CONTEXT, )
    elif len(groupchat.messages) < MAX_ITER:
      await cl.make_async(user_proxy.send)( manager, message=CONTEXT, )
    elif len(groupchat.messages) == MAX_ITER:  
      await cl.make_async(user_proxy.send)( manager, message="exit", )

    # Save agent and retriever messages generated during this turn
    if db_mgr and session_id:
        for m in groupchat.messages[1:]:
            content = m.get("content", "")
            if content.strip() == "TERMINATE" or not content.strip():
                continue
            sender = m.get("name") or m.get("sender", "Agent")
            role = m.get("role", "assistant")
            await db_mgr.save_message(session_id, sender=sender, role=role, content=content)
