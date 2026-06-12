import autogen
from rich import print
import chainlit as cl
import uuid
import os
import shutil
import asyncio
import hashlib
from typing import Annotated
from chainlit.input_widget import (
   Select, Slider, Switch)
from autogen import AssistantAgent
from src.agents import ChainlitUserProxyAgent, ChainlitAssistantAgent
from graphrag.query.cli import run_global_search, run_local_search
from src.adapters.db import DatabaseManager
from src.adapters.vector_store import ChromaManager
import requests
import ollama

def calculate_md5(filepath: str) -> str:
    """Computes MD5 checksum of a file to check for content modifications."""
    hash_md5 = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

async def run_indexing_pipeline(files, db_mgr, chroma_mgr, session_id):
    try:
        # Create directories
        os.makedirs("input/pdfs", exist_ok=True)
        os.makedirs("input/markdown", exist_ok=True)

        status_msg = await cl.Message(content="🔍 *Checking for document modifications (Incremental Engine)...*", author="System").send()

        # Compute MD5 hashes and find new or changed files
        new_or_changed_files = []
        for f in files:
            current_hash = calculate_md5(f.path)
            stored_hash = await db_mgr.get_file_hash(f.name)
            if stored_hash != current_hash:
                new_or_changed_files.append((f, current_hash))

        if not new_or_changed_files:
            await status_msg.update(content="ℹ️ *All uploaded documents are already indexed and up-to-date. Skipping indexing.*")
            # Trigger greeting so conversation can start
            msg = cl.Message(content="Hello! What task would you like to get done today?", author="User_Proxy")
            await msg.send()
            return

        # Notify which files are being indexed
        file_list_str = ", ".join([f.name for f, _ in new_or_changed_files])
        await status_msg.update(content=f"📥 *Processing new/changed files:* `{file_list_str}`\n*Saving files...*")

        # Save files and update their stored hashes in database
        has_pdfs = False
        for f, file_hash in new_or_changed_files:
            dest_dir = "input/pdfs" if f.name.endswith(".pdf") else "input/markdown"
            dest_name = f.name
            if f.name.endswith(".pdf"):
                has_pdfs = True
            if f.name.endswith(".txt"):
                dest_name = f.name[:-4] + ".md"
            
            dest_path = os.path.join(dest_dir, dest_name)
            shutil.copy(f.path, dest_path)
            await db_mgr.update_file_hash(f.name, file_hash)
            
        # Run PDF-to-Markdown parsing only if new PDF files are uploaded!
        if has_pdfs:
            await status_msg.update(content="📑 *PDF files detected. Launching marker parser...*")
            proc = await asyncio.create_subprocess_exec(
                "python3", "-m", "src.tools.pdf_to_markdown",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                print(f"pdf_to_markdown error: {stderr.decode()}")

        await status_msg.update(content="⚡ *Initiating GraphRAG Indexing (this may take a few minutes)...*")

        # Run GraphRAG Indexing
        proc = await asyncio.create_subprocess_exec(
            "python3", "-m", "graphrag.index", "--root", ".",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Stream progress logs
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            line_str = line.decode().strip()
            if "workflow" in line_str.lower() or "percent" in line_str.lower():
                await status_msg.update(content=f"⚙️ *GraphRAG Indexing progress:*\n`{line_str}`")

        await proc.wait()
        
        if proc.returncode != 0:
            err_out = await proc.stderr.read()
            await cl.Message(content=f"❌ *Indexing failed with error:*\n`{err_out.decode()[:500]}`", author="System").send()
            return

        await status_msg.update(content="🔄 *GraphRAG indexing complete. Synchronizing vector records to ChromaDB...*")

        # Sync Parquet to ChromaDB
        parquet_path = chroma_mgr.get_latest_output_parquet()
        if parquet_path:
            success = await cl.make_async(chroma_mgr.sync_entities_from_parquet)(parquet_path)
            if success:
                await status_msg.update(content="🎉 *Workspace successfully indexed! ChromaDB and GraphRAG are synchronized and ready to query.*")
            else:
                await status_msg.update(content="⚠️ *Indexing complete, but ChromaDB synchronization failed.*")
        else:
            await status_msg.update(content="⚠️ *Indexing complete, but could not locate the output parquet files.*")

        # Prompt greeting message after successful indexing
        msg = cl.Message(content="Hello! What task would you like to get done today?", author="User_Proxy")
        await msg.send()

    except Exception as e:
        await cl.Message(content=f"❌ *An unexpected error occurred during indexing: {str(e)}*", author="System").send()

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

    # Initialize ChromaDB manager
    chroma_mgr = ChromaManager()
    cl.user_session.set("chroma_mgr", chroma_mgr)
    
    # Sync from latest parquet entities asynchronously if available
    parquet_path = chroma_mgr.get_latest_output_parquet()
    if parquet_path:
        await cl.make_async(chroma_mgr.sync_entities_from_parquet)(parquet_path)

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
            
            # Offer document uploader
            await cl.Message(content="*Initiated a fresh workspace session.*", author="System").send()
            files = await cl.AskFileMessage(
                content="Would you like to upload document files (.pdf, .txt) to index into the knowledge graph? Or click Cancel to proceed with the existing index.",
                accept=["application/pdf", "text/plain"],
                max_files=5,
                timeout=60
            ).send()
            
            if files:
                asyncio.create_task(run_indexing_pipeline(files, db_mgr, chroma_mgr, session_id))
            else:
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
        
        # Offer document uploader
        await cl.Message(content="*Initiated a fresh workspace session.*", author="System").send()
        files = await cl.AskFileMessage(
            content="Would you like to upload document files (.pdf, .txt) to index into the knowledge graph? Or click Cancel to proceed with the existing index.",
            accept=["application/pdf", "text/plain"],
            max_files=5,
            timeout=60
        ).send()
        
        if files:
            asyncio.create_task(run_indexing_pipeline(files, db_mgr, chroma_mgr, session_id))
        else:
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
            # Query ChromaDB first for instant vector similarity matches
            chroma_mgr = cl.user_session.get("chroma_mgr")
            if chroma_mgr:
                try:
                    embed_res = await ollama.AsyncClient().embed(model="nomic-embed-text", input=question)
                    query_embedding = embed_res["embeddings"][0]
                    matched_entities = chroma_mgr.query_entities(query_embedding, top_k=5)
                    if matched_entities:
                        bullet_points = "\n".join([
                            f"- **{e['name']}** ({e['type']}): {e['description'][:150]}..."
                            for e in matched_entities
                        ])
                        await cl.Message(
                            content=f"🔍 *ChromaDB matched entities:*\n\n{bullet_points}",
                            author="ChromaDB"
                        ).send()
                except Exception as ve:
                    print(f"ChromaDB lookup failed: {ve}")

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
