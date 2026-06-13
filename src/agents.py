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

    def execute_code_blocks(self, code_blocks):
        import os
        import shutil

        # Determine the work directory
        work_dir = self.code_execution_config.get("work_dir", "coding") if self.code_execution_config else "coding"
        
        # Scan for existing images to avoid duplicate rendering of old files
        pre_existing_files = set()
        if os.path.exists(work_dir):
            pre_existing_files = set(os.listdir(work_dir))
            
        # Execute the code blocks
        exit_code, logs = super().execute_code_blocks(code_blocks)
        
        # Scan for newly created files
        if os.path.exists(work_dir):
            # Create an archive directory to store output images so they don't get scanned again
            archive_dir = os.path.join(work_dir, "archive")
            os.makedirs(archive_dir, exist_ok=True)
            
            for file in os.listdir(work_dir):
                if os.path.isdir(os.path.join(work_dir, file)):
                    continue
                if file not in pre_existing_files and file.lower().endswith((".png", ".jpg", ".jpeg")):
                    file_path = os.path.join(work_dir, file)
                    # We will copy the file to a permanent path in the archive
                    archive_path = os.path.join(archive_dir, file)
                    try:
                        shutil.copy2(file_path, archive_path)
                        # Display the image in Chainlit
                        cl.run_sync(
                            cl.Message(
                                content=f"📊 **Visual Plot Generated:** `{file}`",
                                elements=[
                                    cl.Image(path=archive_path, name=file, display="inline")
                                ],
                                author="System"
                            ).send()
                        )
                    except Exception as img_err:
                        print(f"Error copying/sending generated plot image: {img_err}")
                        
        return exit_code, logs
