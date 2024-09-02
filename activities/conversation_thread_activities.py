import os
import json
import requests
from dataclasses import dataclass
from temporalio import activity
from openai import AsyncAssistantEventHandler, AsyncOpenAI, OpenAI
from functions.common import process_function_calls
from typing import Deque, List, Optional, Tuple
from dotenv import load_dotenv
import redis

load_dotenv()

REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = os.getenv("REDIS_PORT")

redis_client = redis.StrictRedis(host=REDIS_HOST, port=REDIS_PORT, db=0)


"""
    Event handler class for handling various events in the assistant.
"""
class EventHandler(AsyncAssistantEventHandler):
    def __init__(self, thread_id: str, run_id: str = "") -> None:
        super().__init__()
        self.events: list = []
        self.result: ConversationThreadMessageResponse = ConversationThreadMessageResponse(
            thread_id=thread_id, 
            run_id=run_id
        )

    async def on_event(self, event) -> None:
        self.events.append(event)

        if(event.event == "thread.run.failed"):
            self.result.failed = True
            self.result.error_code = event.data.last_error.code
            self.result.error_message = event.data.last_error.message

    async def on_text_created(self, text) -> None:
        print("Received text created event: " + text.value)
        redis_client.publish(self.result.thread_id, json.dumps({"e": "on_text_created", "v": text.value}))
        self.result.message = text.value

    async def on_text_delta(self, delta, snapshot):
        redis_client.publish(self.result.thread_id, json.dumps({"e": "on_text_delta", "v": delta.value}))
        if not delta.annotations:
            self.result.message += delta.value

    async def on_text_done(self, text):
        redis_client.publish(self.result.thread_id, json.dumps({"e": "on_text_done", "v": text.value}))
        print("Received text done event: " + text.value)
        self.result.message = text.value

    async def on_tool_call_created(self, tool_call):
        self.result.tool_call_needed = True
        self.result.tool_call_id = tool_call.id

    async def on_tool_call_delta(self, delta, snapshot):
        print("Tool Call Delta, delta", delta)

    """
    When tool call is done streaming, it will update the step with the output and end time
    """
    async def on_tool_call_done(self, tool_call):
        print("Tool Call Done, ID:", tool_call)
        # When tool call is fully received, trigger our function to do smth with it
        await self.handle_required_action(tool_call)

    """
    Custom handler for a function call that requires action
    """
    async def handle_required_action(self, tool_call):
        self.result.tool_call_needed = True
        self.result.tool_type = tool_call.type
        self.result.run_id = self.current_run.id
        print("Handle required action: ", tool_call)
        tool_outputs = []
        # Here we check what function had been called and do smth with it
        if tool_call.type == "file_search":
            print("file_search")
            pass
        elif tool_call.type == "function":
            print("function_call")
            # Parse the arguments
            self.result.tool_call_function_name = tool_call.function.name
            self.result.tool_arguments = json.loads(tool_call.function.arguments)




@dataclass
class ToolCallRequest:
    tool_call_id: str
    tool_function_name: str
    tool_arguments: dict

@dataclass
class ToolCallResult:
    tool_call_id: str
    output: str

@dataclass
class SubmitToolOutputsRequest:
    thread_id: str
    run_id: str
    tool_outputs: list[ToolCallResult]

@dataclass
class ConversationThreadMessageResponse:
    thread_id: str
    run_id: str = ""
    message: str = ""
    tool_call_needed: bool = False
    tool_call_id: str = ""
    tool_call_function_name: str = ""
    tool_type: str = ""
    tool_arguments: Optional[dict] = None

    failed: bool = False
    error_code: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class ConversationThreadMessage:
    thread_id: str
    message: str
    role: str = "user"

class ConversationThreadActivities:
    def __init__(self, openai_api_key: str, openai_gateway_url: str, openai_assistant_id: str) -> None:
        self.openai_assistant_id = openai_assistant_id
        self.openai_client = AsyncOpenAI(api_key=openai_api_key, base_url=openai_gateway_url)

    @activity.defn
    async def create_thread(self) -> str:
        thread = await self.openai_client.beta.threads.create()

        activity.logger.info("Successfully created a thread: " + thread.id)

        return thread.id

    @activity.defn
    async def add_message_to_thread(self, message: ConversationThreadMessage):
        await self.openai_client.beta.threads.messages.create(
            thread_id=message.thread_id,
            role=message.role,
            content=message.message,
        )

    @activity.defn
    async def get_city(self, remote_ip_address: str) -> str:
        url = f"http://ip-api.com/json/{remote_ip_address}"
        params = {}
        headers = {"Content-Type": "application/json"}
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        return data["city"]

    @activity.defn
    async def get_response(self, thread_id: str) -> ConversationThreadMessageResponse:
        assistant = await self.openai_client.beta.assistants.retrieve(
            assistant_id=self.openai_assistant_id
        )

        event_handler = EventHandler(thread_id=thread_id)

        # Create and Stream a Run
        async with self.openai_client.beta.threads.runs.stream(
            thread_id=thread_id,
            assistant_id=assistant.id,
            event_handler=event_handler,
        ) as stream:
            await stream.until_done()

            return event_handler.result

    @activity.defn
    async def function_call(self, request: ToolCallRequest) -> ToolCallResult:
        result = process_function_calls(
            request.tool_function_name,
            request.tool_arguments,
        )

        return ToolCallResult(tool_call_id=request.tool_call_id, output=result)

    @activity.defn
    async def submit_tool_call_result(self, request: SubmitToolOutputsRequest) -> ConversationThreadMessageResponse:
        event_handler = EventHandler(thread_id=request.thread_id, run_id=request.run_id)

        async with self.openai_client.beta.threads.runs.submit_tool_outputs_stream(
            thread_id=request.thread_id,
            run_id=request.run_id,
            tool_outputs=[{"tool_call_id": x.tool_call_id, "output": x.output} for x in request.tool_outputs],
            event_handler=event_handler,
        ) as stream:
            await stream.until_done()
            print("------------- submit_tool_call_result events: --------------")
            print(event_handler.events)

            return event_handler.result