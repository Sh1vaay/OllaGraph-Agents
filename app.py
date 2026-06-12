import autogen
from rich import print
import chainlit as cl
from typing import Annotated
from chainlit.input_widget import (
   Select, Slider, Switch)
from autogen import AssistantAgent
from src.agents import ChainlitUserProxyAgent, ChainlitAssistantAgent
from graphrag.query.cli import run_global_search, run_local_search

# Ollama LLM for Agents
llm_config_autogen = {
    "seed": 42,  # change the seed for different trials
    "temperature": 0,
    "config_list": [
        {
            "model": "llama3",
            "base_url": "http://localhost:11434/v1",
            "api_key": "ollama",
        }
    ],
    "timeout": 60000,
}

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
       llm_config=llm_config_autogen, 
       system_message="""Only execute the function query_graphRAG to look for context. 
                    Output 'TERMINATE' when an answer has been provided.""",
       max_consecutive_auto_reply=1,
       human_input_mode="NEVER", 
       description="Retriever Agent"
     )

    user_proxy = ChainlitUserProxyAgent(
        name="User_Proxy",
        human_input_mode="ALWAYS",
        llm_config=llm_config_autogen,
        is_termination_msg=lambda x: x.get("content", "").rstrip().endswith("TERMINATE"),
        code_execution_config=False,
        system_message='''A human admin. Interact with the retriever to provide any context''',
        description="User Proxy Agent"
    )
    
    print("Set agents.")

    cl.user_session.set("Query Agent", user_proxy)
    cl.user_session.set("Retriever", retriever)

    msg = cl.Message(content=f"""Hello! What task would you like to get done today?      
                     """, 
                     author="User_Proxy")
    await msg.send()

    print("Message sent.")
    
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
    user_proxy  = cl.user_session.get("Query Agent")
    print("Setting groupchat")

    def state_transition(last_speaker: autogen.Agent, groupchat: autogen.GroupChat) -> autogen.Agent | None:
        if last_speaker is user_proxy:
            return retriever
        if last_speaker is retriever:
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
        agents=[user_proxy, retriever],
        messages=[],
        max_round=MAX_ITER,
        speaker_selection_method=state_transition,
        allow_repeat_speaker=True,
    )
    manager = autogen.GroupChatManager(groupchat=groupchat,
                                       llm_config=llm_config_autogen, 
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
