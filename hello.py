#!/usr/bin/env python3
"""A simple interactive program that asks a few questions and prints a summary."""


def main():
    # Welcome the user and explain what this program does.
    print("Welcome! I'll ask you a few questions about yourself and AI.")
    print()

    # Ask for the user's name and save their answer in a variable.
    name = input("What is your name? ")

    # Ask which AI tool they like most.
    favorite_tool = input("What is your favorite AI tool? ")

    # Ask what they hope to learn about AI.
    learning_goal = input("What do you hope to learn about AI? ")

    # Print a blank line, then a formatted summary of the answers.
    print()
    print("=" * 44)
    print("  Summary")
    print("=" * 44)
    print(f"  Name:             {name}")
    print(f"  Favorite AI tool: {favorite_tool}")
    print(f"  Hope to learn:    {learning_goal}")
    print("=" * 44)
    print()
    print(f"Nice to meet you, {name}! Welcome to Cursor.")


# This runs main() only when you start the file directly
# (for example: python3 hello.py).
if __name__ == "__main__":
    main()
