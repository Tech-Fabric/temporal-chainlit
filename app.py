import os
import asyncio
import redis.asyncio as redis
from openai import AsyncAssistantEventHandler, AsyncOpenAI, OpenAI
from literalai.helper import utc_now
import chainlit as cl
from chainlit.config import config
import json
from functions.common import process_function_calls
from temporalio.client import Client
from dotenv import load_dotenv
import uuid

from workflows.conversation_thread_workflow import ConversationThreadWorkflow, ConversationThreadParams

load_dotenv()

openai_api_key = os.getenv("OPENAI_API_KEY")
openai_assistant_id = os.getenv("OPENAI_ASSISTANT_ID")
openai_gateway_url = os.getenv("OPENAI_GATEWAY_URL")

REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = os.getenv("REDIS_PORT")

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST")
TEMPORAL_PORT = os.getenv("TEMPORAL_PORT")


sync_openai_client = OpenAI(api_key=openai_api_key, base_url=openai_gateway_url)

assistant = sync_openai_client.beta.assistants.retrieve(
    assistant_id=openai_assistant_id
)

config.ui.name = assistant.name

temporal_client: Client = None

redis_client = redis.from_url(
  'redis://' + REDIS_HOST + ':' + REDIS_PORT,
  health_check_interval=10,
  socket_connect_timeout=5,
  retry_on_timeout=True,
  socket_keepalive=True
)


async def get_temporal_client() -> Client:
    global temporal_client
    if not temporal_client:
        temporal_client = await Client.connect(TEMPORAL_HOST + ":" + TEMPORAL_PORT)
    return temporal_client

def get_workflow_id(session: dict):
    return session.get("id", None)

@cl.on_chat_start
async def start_chat():
    client = await get_temporal_client()

    cl.user_session.set("id", str(uuid.uuid4()))

    workflow_id =  get_workflow_id(cl.user_session)

    handle = client.get_workflow_handle(
        workflow_id=workflow_id,
    )

    start_new_workflow: bool = False
    try:
        workflow_status = await handle.describe()

        if workflow_status.status != 1:
            start_new_workflow = True
    except Exception as e:
        start_new_workflow = True

    if start_new_workflow:
        handle = await client.start_workflow(
            ConversationThreadWorkflow.run,
            ConversationThreadParams(
                remote_ip_address="40.40.40.40" # random ip, TODO: add code to get client ip address
            ),
            id=workflow_id,
            task_queue="conversation-workflow-task-queue",
        )

    thread_id = None

    while(not thread_id):
        thread_id = await handle.query(ConversationThreadWorkflow.get_thread_id)
        await asyncio.sleep(1)

    # Store thread ID in user session for later use
    cl.user_session.set("thread_id", thread_id)



@cl.on_message
async def main(message: cl.Message):
    try:
        client = await get_temporal_client()

        workflow_id =  get_workflow_id(cl.user_session)

        handle = client.get_workflow_handle(
            workflow_id=workflow_id,
        )

        thread_id = await handle.query(ConversationThreadWorkflow.get_thread_id)

        async with redis_client.pubsub() as pubsub:
            await pubsub.subscribe(thread_id)

            await handle.signal(ConversationThreadWorkflow.on_message, message.content)

            cl_message = None
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True)
                if message is not None:
                    message_dict = json.loads(message["data"].decode())

                    if(message_dict["e"] == "on_text_created"):
                        cl_message = await cl.Message(
                            author=assistant.name, content=""
                        ).send()
                    elif(message_dict["e"] == "on_text_delta"):
                        await cl_message.stream_token(message_dict["v"])
                    elif(message_dict["e"] == "on_text_done"):
                        await cl_message.update()
                        await pubsub.unsubscribe(thread_id)
                        break


    except Exception as e:
        print("An error occurred:", str(e))
        cl.Message("An error occurred. Please refresh the page and try again.")


@cl.on_chat_end
async def end_chat():
    client = await get_temporal_client()

    workflow_id = get_workflow_id(cl.user_session)

    #thread_id = cl.user_session.get("thread_id")

    handle = client.get_workflow_handle(
        workflow_id=workflow_id,
    )

    await handle.signal(ConversationThreadWorkflow.end_thread)

    #cl.user_session.clear()

    print("Chat Ended")
