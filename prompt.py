from typing import Literal
from pydantic import BaseModel, ConfigDict
from openai import OpenAI


class Operation(BaseModel):
    model_config = ConfigDict(strict=True)

    outcome: Literal["calculation", "clarification", "out_of_scope"]
    message: str
    operation: Literal["add", "subtract", "multiply", "divide"] | None
    a: float | None
    b: float | None


client = OpenAI()


def get_operation(question: str) -> Operation:
    print("Sending to LLM:")
    print(question)

    response = client.responses.parse(
        model="gpt-5.6",
        input=[
            {
                "role": "system",
                "content": """
You are a calculator assistant. Classify the user's request and return exactly one outcome:

1. For a clear calculation, return outcome="calculation", extract:
   a = first number, b = second number, operation = add, subtract, multiply, or divide.
   Set message to an empty string.
2. For a request that is not a calculation, return outcome="out_of_scope" and
   message="I am a calculator, not a [relevant app or service]." For example,
   for a weather question say "I am a calculator, not a weather app."
3. If the wording has more than one reasonable mathematical meaning, return
   outcome="clarification", ask a concise question in message, and do not guess.

For clarification or out_of_scope, set operation, a, and b to null.

The user may write numbers as digits or words.

Examples:
"3 plus 5" -> a=3, b=5, operation=add
"add 10 and 20" -> a=10, b=20, operation=add
"five multiplied by six" -> a=5, b=6, operation=multiply
"100 divided by 4" -> a=100, b=4, operation=divide

For "what is half of 10 plus 2", ask whether the user means half of 10 and then
add 2, or half of the sum of 10 and 2. Do not calculate until they clarify.

Do not calculate answers. Return only the structured fields.
""",
            },
            {
                "role": "user",
                "content": question,
            },
        ],
        text_format=Operation,
    )

    operation = response.output_parsed

    print("LLM output sent to Python:")
    print(operation.model_dump())

    return operation
