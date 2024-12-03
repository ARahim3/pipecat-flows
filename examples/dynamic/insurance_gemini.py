#
# Copyright (c) 2024, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#
"""
Insurance Quote Example using Pipecat Dynamic Flows with Google Gemini

This example demonstrates how to create a conversational insurance quote bot using:
- Dynamic flow management for flexible conversation paths
- Google Gemini for natural language understanding
- Simple function handlers for processing user input
- Node configurations for different conversation states
- Pre/post actions for user feedback

The flow allows users to:
1. Provide their age
2. Specify marital status
3. Get an insurance quote
4. Adjust coverage options
5. Complete the quote process

Requirements:
- Daily room URL
- Google API key
- Deepgram API key
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Dict, TypedDict, Union

import aiohttp
from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.deepgram import DeepgramSTTService, DeepgramTTSService
from pipecat.services.google import GoogleLLMService
from pipecat.transports.services.daily import DailyParams, DailyTransport

sys.path.append(str(Path(__file__).parent.parent))
from runner import configure

from pipecat_flows import FlowArgs, FlowManager, FlowResult, NodeConfig

load_dotenv(override=True)

logger.remove(0)
logger.add(sys.stderr, level="DEBUG")


# Type definitions
class InsuranceQuote(TypedDict):
    monthly_premium: float
    coverage_amount: int
    deductible: int


class AgeCollectionResult(FlowResult):
    age: int


class MaritalStatusResult(FlowResult):
    marital_status: str


class QuoteCalculationResult(FlowResult, InsuranceQuote):
    pass


class CoverageUpdateResult(FlowResult, InsuranceQuote):
    pass


# Simulated insurance data
INSURANCE_RATES = {
    "young_single": {"base_rate": 150, "risk_multiplier": 1.5},
    "young_married": {"base_rate": 130, "risk_multiplier": 1.3},
    "adult_single": {"base_rate": 100, "risk_multiplier": 1.0},
    "adult_married": {"base_rate": 90, "risk_multiplier": 0.9},
}


# Function handlers
async def collect_age(args: FlowArgs) -> AgeCollectionResult:
    """Process age collection."""
    age = args["age"]
    logger.debug(f"collect_age handler executing with age: {age}")
    return {"age": age}


async def collect_marital_status(args: FlowArgs) -> MaritalStatusResult:
    """Process marital status collection."""
    status = args["marital_status"]
    logger.debug(f"collect_marital_status handler executing with status: {status}")
    return {"marital_status": status}


async def calculate_quote(args: FlowArgs) -> QuoteCalculationResult:
    """Calculate insurance quote based on age and marital status."""
    age = args["age"]
    marital_status = args["marital_status"]
    logger.debug(f"calculate_quote handler executing with age: {age}, status: {marital_status}")

    # Determine rate category
    age_category = "young" if age < 25 else "adult"
    rate_key = f"{age_category}_{marital_status}"
    rates = INSURANCE_RATES.get(rate_key, INSURANCE_RATES["adult_single"])

    # Calculate quote
    monthly_premium = rates["base_rate"] * rates["risk_multiplier"]

    return {
        "monthly_premium": monthly_premium,
        "coverage_amount": 250000,
        "deductible": 1000,
    }


async def update_coverage(args: FlowArgs) -> CoverageUpdateResult:
    """Update coverage options and recalculate premium."""
    coverage_amount = args["coverage_amount"]
    deductible = args["deductible"]
    logger.debug(
        f"update_coverage handler executing with amount: {coverage_amount}, deductible: {deductible}"
    )

    # Calculate adjusted quote
    monthly_premium = (coverage_amount / 250000) * 100
    if deductible > 1000:
        monthly_premium *= 0.9  # 10% discount for higher deductible

    return {
        "monthly_premium": monthly_premium,
        "coverage_amount": coverage_amount,
        "deductible": deductible,
    }


async def end_quote() -> FlowResult:
    """Handle quote completion."""
    logger.debug("end_quote handler executing")
    return {"status": "completed"}


# Node configurations
def create_initial_node() -> NodeConfig:
    """Create the initial node asking for age."""
    return {
        "messages": [
            {
                "role": "system",
                "content": "You are an insurance agent. Start by asking for the customer's age.",
            }
        ],
        "functions": [
            {
                "type": "function",
                "function": {
                    "name": "collect_age",
                    "handler": collect_age,
                    "description": "Record customer's age",
                    "parameters": {
                        "type": "object",
                        "properties": {"age": {"type": "integer"}},
                        "required": ["age"],
                    },
                },
            }
        ],
        "pre_actions": [
            {"type": "tts_say", "text": "Welcome! Let's find the right insurance coverage for you."}
        ],
    }


def create_marital_status_node() -> NodeConfig:
    """Create node for collecting marital status."""
    return {
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ask about the customer's marital status (single or married). "
                    "Wait for their response before calling collect_marital_status. "
                    "Only call the function after they provide their status."
                ),
            }
        ],
        "functions": [
            {
                "function_declarations": [
                    {
                        "name": "collect_marital_status",
                        "handler": collect_marital_status,
                        "description": "Record marital status after customer provides it",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "marital_status": {"type": "string", "enum": ["single", "married"]}
                            },
                            "required": ["marital_status"],
                        },
                    }
                ]
            }
        ],
        "pre_actions": [{"type": "tts_say", "text": "Now, I'll need to know your marital status."}],
    }


def create_quote_calculation_node(age: int, marital_status: str) -> NodeConfig:
    """Create node for calculating initial quote."""
    return {
        "messages": [
            {
                "role": "system",
                "content": (
                    f"Calculate a quote for {age} year old {marital_status} customer. "
                    "Call calculate_quote with their information. "
                    "After receiving the quote, explain the details and ask if they'd like to adjust coverage."
                ),
            }
        ],
        "functions": [
            {
                "function_declarations": [
                    {
                        "name": "calculate_quote",
                        "handler": calculate_quote,
                        "description": "Calculate initial insurance quote",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "age": {"type": "integer"},
                                "marital_status": {"type": "string", "enum": ["single", "married"]},
                            },
                            "required": ["age", "marital_status"],
                        },
                    }
                ]
            }
        ],
    }


def create_quote_results_node(
    quote: Union[QuoteCalculationResult, CoverageUpdateResult],
) -> NodeConfig:
    """Create node for showing quote and adjustment options."""
    return {
        "messages": [
            {
                "role": "system",
                "content": (
                    f"Quote details:\n"
                    f"Monthly Premium: ${quote['monthly_premium']:.2f}\n"
                    f"Coverage Amount: ${quote['coverage_amount']:,}\n"
                    f"Deductible: ${quote['deductible']:,}\n\n"
                    "Explain these quote details to the customer. "
                    "Ask if they would like to adjust the coverage amount or deductible. "
                    "They can also end the quote process if they're satisfied."
                ),
            }
        ],
        "functions": [
            {
                "function_declarations": [
                    {
                        "name": "update_coverage",
                        "handler": update_coverage,
                        "description": "Update coverage options when customer requests changes",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "coverage_amount": {"type": "integer"},
                                "deductible": {"type": "integer"},
                            },
                            "required": ["coverage_amount", "deductible"],
                        },
                    },
                    {
                        "name": "end_quote",
                        "handler": end_quote,
                        "description": "Complete the quote process when customer is satisfied",
                        "parameters": {
                            "type": "object",
                            "properties": {"status": {"type": "string", "enum": ["completed"]}},
                            "required": ["status"],
                        },
                    },
                ]
            }
        ],
    }


def create_end_node() -> NodeConfig:
    """Create the final node."""
    return {
        "messages": [
            {
                "role": "system",
                "content": (
                    "Thank the customer for their time and end the conversation. "
                    "Mention that a representative will contact them about the quote."
                ),
            }
        ],
        "functions": [],
        "pre_actions": [
            {"type": "tts_say", "text": "Thank you for getting a quote with us today!"}
        ],
        "post_actions": [{"type": "end_conversation"}],
    }


# Transition callback
async def handle_insurance_transition(function_name: str, args: Dict, flow_manager: FlowManager):
    """Handle transitions between insurance flow states."""
    logger.debug(f"Handling transition for function: {function_name} with args: {args}")

    if function_name == "collect_age":
        flow_manager.state["age"] = args["age"]
        await flow_manager.set_node("marital_status", create_marital_status_node())

    elif function_name == "collect_marital_status":
        flow_manager.state["marital_status"] = args["marital_status"]
        await flow_manager.set_node(
            "quote_calculation",
            create_quote_calculation_node(
                flow_manager.state["age"], flow_manager.state["marital_status"]
            ),
        )

    elif function_name == "calculate_quote":
        # Calculate the quote using the handler
        quote = await calculate_quote(args)
        flow_manager.state["quote"] = quote
        await flow_manager.set_node("quote_results", create_quote_results_node(quote))

    elif function_name == "update_coverage":
        # Calculate updated quote using the handler
        updated_quote = await update_coverage(args)
        flow_manager.state["quote"] = updated_quote
        await flow_manager.set_node("quote_results", create_quote_results_node(updated_quote))

    elif function_name == "end_quote":
        await flow_manager.set_node("end", create_end_node())


async def main():
    """Main function to set up and run the insurance quote bot."""
    async with aiohttp.ClientSession() as session:
        (room_url, _) = await configure(session)

        # Initialize services
        transport = DailyTransport(
            room_url,
            None,
            "Insurance Quote Bot",
            DailyParams(
                audio_out_enabled=True,
                vad_enabled=True,
                vad_analyzer=SileroVADAnalyzer(),
                vad_audio_passthrough=True,
            ),
        )

        stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))
        tts = DeepgramTTSService(api_key=os.getenv("DEEPGRAM_API_KEY"), voice="aura-helios-en")
        llm = GoogleLLMService(api_key=os.getenv("GOOGLE_API_KEY"), model="gemini-1.5-flash-latest")

        # Create initial context
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a friendly insurance agent. Your responses will be "
                    "converted to audio, so avoid special characters. "
                    "Always wait for customer responses before calling functions. "
                    "Only call functions after receiving relevant information from the customer."
                ),
            }
        ]

        context = OpenAILLMContext(messages, [])
        context_aggregator = llm.create_context_aggregator(context)

        # Create pipeline
        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                context_aggregator.user(),
                llm,
                tts,
                transport.output(),
                context_aggregator.assistant(),
            ]
        )

        task = PipelineTask(pipeline, PipelineParams(allow_interruptions=True))

        # Initialize flow manager with transition callback
        flow_manager = FlowManager(task, llm, tts, transition_callback=handle_insurance_transition)

        @transport.event_handler("on_first_participant_joined")
        async def on_first_participant_joined(transport, participant):
            await transport.capture_participant_transcription(participant["id"])
            # Initialize flow
            await flow_manager.initialize(messages)
            # Set initial node
            await flow_manager.set_node("initial", create_initial_node())
            await task.queue_frames([context_aggregator.user().get_context_frame()])

        # Run the pipeline
        runner = PipelineRunner()
        await runner.run(task)


if __name__ == "__main__":
    asyncio.run(main())
