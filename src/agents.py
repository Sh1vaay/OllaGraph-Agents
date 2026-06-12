from collections.abc import Callable
from autogen.agentchat import Agent, AssistantAgent, UserProxyAgent
import chainlit as cl

async def ask_helper(func: Callable, **kwargs) -> dict:
    res = await func(**kwargs).send()
    while not res:
        res = await func(**kwargs).send()
    return res

class ChainlitAssistantAgent(AssistantAgent):
    """
    Wrapper for AutoGen's Assistant Agent.
    """
    def send(
        self,
        message: dict | str,
        recipient: Agent,
        request_reply: bool | None = None,
        silent: bool = False,
    ) -> bool:
        cl.run_sync(
            cl.Message(
                content=f'*Sending message to "{recipient.name}":*\n\n{message}',
                author=self.name,
            ).send()
        )
        return super().send(
            message=message,
            recipient=recipient,
            request_reply=request_reply,
            silent=silent,
        )
        
class ChainlitUserProxyAgent(UserProxyAgent):
    """
    Wrapper for AutoGen's UserProxy Agent. Simplifies the UI by adding CL Actions.
    """
    def get_human_input(self, prompt: str) -> str:
        if prompt.startswith(
            "Provide feedback to chat_manager. Press enter to skip and use auto-reply"
        ):
            res = cl.run_sync(
                ask_helper(
                    cl.AskActionMessage,
                    content="Continue or provide feedback?",
                    actions=[
                        cl.Action( name="continue", value="continue", label="✅ Continue" ),
                        cl.Action( name="feedback",value="feedback", label="💬 Provide feedback"),
                        cl.Action( name="exit",value="exit", label="🔚 Exit Conversation" )
                    ],
                )
            )
            if res.get("value") == "continue":
                return ""
            if res.get("value") == "exit":
                return "exit"

        reply = cl.run_sync(ask_helper(cl.AskUserMessage, content=prompt, timeout=60))

        return reply["output"].strip()

    def send(
        self,
        message: dict | str,
        recipient: Agent,
        request_reply: bool | None = None,
        silent: bool = False,
    ):
        super().send(
            message=message,
            recipient=recipient,
            request_reply=request_reply,
            silent=silent,
        )
