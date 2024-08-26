import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from typing import Deque, List, Optional, Tuple

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from openai import AsyncAssistantEventHandler, AsyncOpenAI, OpenAI
    from activities.conversation_thread_activities import ConversationThreadActivities, ConversationThreadMessage, ToolCallRequest, ToolCallResult, SubmitToolOutputsRequest


@dataclass
class ConversationThreadParams:
    remote_ip_address: str

@dataclass
class ConversationMessage:
    author: str = ""
    message: str = ""

@workflow.defn
class ConversationThreadWorkflow:
    def __init__(self) -> None:
        self.thread_closed: bool = False
        self.thread_id: str = ""
        self.params = None
        self.remote_city: str = ""
        self.messages: list[ConversationMessage] = list()

    @workflow.run
    async def run(
        self,
        params: ConversationThreadParams,
    ) -> str:
        self.params = params

        response = await workflow.execute_activity_method(
            ConversationThreadActivities.create_thread,
            schedule_to_close_timeout=timedelta(seconds=5),
            retry_policy=RetryPolicy(
                initial_interval= timedelta(seconds=2),
                backoff_coefficient= 2.0,
                maximum_interval= None,
                maximum_attempts= 10,
                non_retryable_error_types= None
            )
        )

        workflow.logger.info("Created thread: " + response)

        self.thread_id = response

        print("------------------- thread_id: " + self.thread_id)

        self.remote_city = await workflow.execute_activity_method(
            ConversationThreadActivities.get_city,
            params.remote_ip_address,
            schedule_to_close_timeout=timedelta(seconds=5),
            retry_policy=RetryPolicy(
                initial_interval= timedelta(seconds=2),
                backoff_coefficient= 2.0,
                maximum_interval= None,
                maximum_attempts= 10,
                non_retryable_error_types= None
            )
        )

        await workflow.wait_condition(lambda: self.thread_closed)

        return "thread closed"

    @workflow.query
    def get_thread_id(self) -> Optional[str]:
        return self.thread_id

    @workflow.query
    def get_history(self) -> list[ConversationMessage]:
        return self.messages

    @workflow.signal
    async def on_message(self, message: str):
        await workflow.execute_activity_method(
            ConversationThreadActivities.add_message_to_thread,
            ConversationThreadMessage(thread_id=self.thread_id, message=message),
            schedule_to_close_timeout=timedelta(seconds=5),
        )

        self.messages.append(ConversationMessage(author="", message=message))

        response = await workflow.execute_activity_method(
            ConversationThreadActivities.get_response,
            self.thread_id,
            schedule_to_close_timeout=timedelta(seconds=120),
        )

        print("----------------- Response ---------------")
        print(response)

        while(response.tool_call_needed):
            tool_arguments = response.tool_arguments
            tool_arguments.update({
                "city": self.remote_city,
            })

            tool_call_result = await workflow.execute_activity_method(
                ConversationThreadActivities.function_call,
                ToolCallRequest(
                    tool_call_id=response.tool_call_id, 
                    tool_function_name=response.tool_call_function_name, 
                    tool_arguments=response.tool_arguments
                ),
                schedule_to_close_timeout=timedelta(seconds=120),
            )

            print("-------------- Tool call result -------------")
            print(tool_call_result)

            response = await workflow.execute_activity_method(
                ConversationThreadActivities.submit_tool_call_result,
                SubmitToolOutputsRequest(
                    thread_id=response.thread_id,
                    run_id=response.run_id,
                    tool_outputs=[tool_call_result]
                ),
                schedule_to_close_timeout=timedelta(seconds=120),
            )

        print("------------- Done get_response--------------------")
        print(response)

        self.messages.append(ConversationMessage(author="assistant", message=response.message))

    @workflow.signal
    async def end_thread(self):
        self.thread_closed = True