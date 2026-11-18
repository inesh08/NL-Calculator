
from dotenv import load_dotenv

load_dotenv()

import zoroval

zoroval.init()  # reads ZOROVAL_INGEST_KEY; wraps OpenAI; never raises

from calculator import calculate
from interactions import get_tracked_operation
from storage import initialize_database, save_exchange
import uuid
import logging

logger = logging.getLogger("nl_calculator.terminal")


def main():
    try:
        initialize_database()
    except Exception:
        logger.exception("Database initialization failed; conversation saving will be unavailable")
    question = input("What calculation do you want to do? ")
    conversation_id = "terminal-" + uuid.uuid4().hex
    visible_question = question

    while True:
        # One terminal session is one Zoroval conversation; each question is one turn.
        with zoroval.conversation(conversation_id), zoroval.turn(input=visible_question) as turn:
            try:
                operation = get_tracked_operation(question)

                if operation.outcome == "out_of_scope":
                    print(operation.message)
                    turn.set_output(operation.message)
                    save_exchange(source="terminal", conversation_id=conversation_id,
                                  question=visible_question, answer=operation.message)
                    return

                if operation.outcome == "clarification":
                    turn.set_output(operation.message)
                    clarification = input(operation.message + " ")
                    save_exchange(source="terminal", conversation_id=conversation_id,
                                  question=visible_question, answer=operation.message)
                    visible_question = clarification

                    question = (
                        f"Original request: {question}\n"
                        f"Clarification question: {operation.message}\n"
                        f"User's clarification: {clarification}"
                    )

                    continue

                result = calculate(operation)

                print("Result:", result)
                turn.set_output(f"Result: {result}")
                save_exchange(source="terminal", conversation_id=conversation_id,
                              question=visible_question, answer=f"Result: {result}")
                return

            except ValueError as error:
                print("Unable to calculate:", error)
                turn.set_output(f"Unable to calculate: {error}")
                save_exchange(source="terminal", conversation_id=conversation_id,
                              question=visible_question, answer=f"Unable to calculate: {error}")
                return

            except Exception as error:
                print("Unable to process the request:", error)
                turn.set_output(f"Unable to process the request: {error}")
                save_exchange(source="terminal", conversation_id=conversation_id,
                              question=visible_question, answer=f"Unable to process the request: {error}")
                return


if __name__ == "__main__":
    main()

