from typing import Literal

from pydantic import BaseModel



from openai import OpenAI
from pydantic import BaseModel, ConfigDict, ValidationError


class Operation(BaseModel):
    model_config = ConfigDict(strict=True)

    outcome: Literal["calculation", "clarification", "out_of_scope"]
    message: str
    operation: Literal["add", "subtract", "multiply", "divide"] | None
    a: float | None
    b: float | None


client = OpenAI()




def validate_operation(operation: Operation) -> Operation:
    try:
        validated = Operation.model_validate(operation.model_dump())
    except ValidationError as error:
        raise ValueError(f"Invalid model response: {error}") from error

    if validated.outcome == "calculation":
        if validated.operation is None:
            raise ValueError("Invalid model response: missing operation")

        if validated.a is None:
            raise ValueError("Invalid model response: missing first number")

        if validated.b is None:
            raise ValueError("Invalid model response: missing second number")

    elif validated.outcome in {"clarification", "out_of_scope"}:
        if validated.operation is not None:
            raise ValueError("Invalid model response: operation must be null")

        if validated.a is not None:
            raise ValueError("Invalid model response: first number must be null")

        if validated.b is not None:
            raise ValueError("Invalid model response: second number must be null")

    return validated



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

You are a calculator assistant.

Classify the user's request into exactly one of these outcomes:

1. calculation
   Use this when the user clearly asks for a calculation involving two numbers.
   Extract the first number into a.
   Extract the second number into b.
   Use one of these operations:
   add
   subtract
   multiply
   divide
   Set message to an empty string.

2. clarification
   Use this when the request is mathematical but has more than one reasonable
   interpretation.
   Ask a short clarification question in message.
   Set operation, a, and b to null.


3. out_of_scope
   Use this when the request is not a calculator request.
   Set message to a short explanation.
   Set operation, a, and b to null.

The user can enter numbers as digits or words.

Examples:


Do not calculate the answer.
Only return a, b, and operation.

"3 plus 5"
a = 3
b = 5
operation = add

"five multiplied by six"
a = 5
b = 6
operation = multiply

"100 divided by 4"
a = 100
b = 4
operation = divide

Do not calculate the answer.
Only extract and classify the user's request.

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

    if response.output_parsed is None:
        raise ValueError("The model did not return a valid structured response")

    return validate_operation(response.output_parsed)
