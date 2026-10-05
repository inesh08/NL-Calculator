from typing import Literal
from pydantic import BaseModel
from openai import OpenAI


class Operation(BaseModel):
    operation: Literal["add", "subtract", "multiply", "divide"]
    a: float
    b: float


client = OpenAI()


def get_operation(question: str) -> Operation:
    response = client.responses.parse(
        model="gpt-5.6",
        input=[
            {
                "role": "system",
                "content": """
Read the user's calculation and extract:

a = first number
b = second number
operation = add, subtract, multiply, or divide

The user may write numbers as digits or words.

Examples:
"3 plus 5" -> a=3, b=5, operation=add
"add 10 and 20" -> a=10, b=20, operation=add
"five multiplied by six" -> a=5, b=6, operation=multiply
"100 divided by 4" -> a=100, b=4, operation=divide

Do not calculate the answer.
Only return a, b, and operation.
""",
            },
            {
                "role": "user",
                "content": question,
            },
        ],
        text_format=Operation,
    )

    return response.output_parsed