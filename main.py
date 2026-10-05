from prompt import get_operation
from calculate import calculate

def main():
    a = float(input("Enter the first number: "))
    b = float(input("Enter the second number: "))
    operation = input("Enter the operation (add, subtract, multiply, divide): ")
    result = get_operation(a, b, operation)
    answer = calculate(result)
    print("answer: ", answer)

if __name__ == "__main__":
    main()