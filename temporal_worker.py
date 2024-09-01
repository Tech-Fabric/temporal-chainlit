import asyncio
import concurrent.futures
import logging
import os

from temporalio.client import Client
from temporalio.worker import Worker

from workflows.conversation_thread_workflow import ConversationThreadWorkflow

from activities.conversation_thread_activities import ConversationThreadActivities


openai_api_key = os.getenv("OPENAI_API_KEY")
openai_assistant_id = os.getenv("OPENAI_ASSISTANT_ID")
openai_gateway_url = os.getenv("OPENAI_GATEWAY_URL")

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST")
TEMPORAL_PORT = os.getenv("TEMPORAL_PORT")

temporal_connection_string = TEMPORAL_HOST + ":" + TEMPORAL_PORT
print(temporal_connection_string)

async def main():
    while(True):
        try:
            client = await Client.connect(temporal_connection_string)

            conversation_thread_activities = ConversationThreadActivities(openai_api_key, openai_gateway_url, openai_assistant_id)

            with concurrent.futures.ThreadPoolExecutor(max_workers=100) as activity_executor:
                worker = Worker(
                    client,
                    task_queue="conversation-workflow-task-queue",
                    workflows=[ConversationThreadWorkflow],
                    activities=[
                        conversation_thread_activities.create_thread,
                        conversation_thread_activities.add_message_to_thread,
                        conversation_thread_activities.get_response,
                        conversation_thread_activities.function_call,
                        conversation_thread_activities.get_city,
                        conversation_thread_activities.submit_tool_call_result,
                    ],
                    activity_executor=activity_executor,
                )
                await worker.run()
        except RuntimeError as e:
            print(e)
            await asyncio.sleep(5)


if __name__ == "__main__":
    print("Starting worker")

    logging.basicConfig(level=logging.INFO)

    asyncio.run(main())