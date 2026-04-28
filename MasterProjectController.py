#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Jan 29 04:17:44 2026

@author: haze1079
"""

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import serial
import time
import os

# -------- CONFIG --------
PORT = "/dev/cu.usbmodem1101" 
BAUD = 9600
DOT_DURATION = 0.2
DASH_DURATION = DOT_DURATION * 3
SYMBOL_SPACE = DOT_DURATION
LETTER_SPACE = DOT_DURATION * 3
WORD_SPACE = DOT_DURATION * 7

MORSE_CODE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", 
    "F": "..-.", "G": "--.", "H": "....", "I": "..", "J": ".---", 
    "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---", 
    "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-", 
    "U": "..-", "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--", "Z": "--.."
}

arduino = serial.Serial(PORT, BAUD)
time.sleep(2)
current_auto_finger = 1

def get_next_auto_finger():
    global current_auto_finger
    finger = current_auto_finger
    current_auto_finger = (current_auto_finger % 5) + 1
    return finger

def send_command(cmd):
    arduino.write((cmd + "\n").encode())

def play_symbol(symbol, finger):
    if symbol == ".":
        send_command(f"SC{finger}E1")
        time.sleep(DOT_DURATION)
    elif symbol == "-":
        send_command(f"SD{finger}E47")
        time.sleep(DASH_DURATION)
    time.sleep(SYMBOL_SPACE)

def play_raw_morse(morse_input):
    """Processes strings like '1... 5---' directly."""
    clusters = morse_input.split(" ")
    for cluster in clusters:
        if not cluster: continue
        
        # Check if first character is a finger number 1-5
        if cluster[0].isdigit() and '1' <= cluster[0] <= '5':
            target_finger = int(cluster[0])
            symbols = cluster[1:] 
        else:
            target_finger = get_next_auto_finger()
            symbols = cluster

        for symbol in symbols:
            if symbol in [".", "-"]:
                play_symbol(symbol, target_finger)
            elif symbol == "/": 
                time.sleep(WORD_SPACE)
        time.sleep(LETTER_SPACE)

def play_parsed_text(text):
    """Parses text for tags like '1HELLO' and rotates fingers."""
    words = text.split(" ")
    for word in words:
        if not word: continue
        
        # Check if first character is a finger number 1-5
        if word[0].isdigit() and '1' <= word[0] <= '5':
            target_finger = int(word[0])
            content = word[1:]
        else:
            target_finger = get_next_auto_finger()
            content = word
            
        for letter in content.upper():
            morse = MORSE_CODE.get(letter)
            if morse:
                for symbol in morse:
                    play_symbol(symbol, target_finger)
                time.sleep(LETTER_SPACE)
        time.sleep(WORD_SPACE)

# -------- MAIN LOOP --------
while True:
    print("\n--- Select Mode ---")
    print("1: Text Input (e.g. '1HELLO 5SOS')")
    print("2: Direct Morse (e.g. '1... 5---')")
    print("3: Read from File (.txt)")
    
    choice = input("Choice (1-3): ")

    if choice == "1":
        user_input = input("Enter text: ")
        play_parsed_text(user_input)

    elif choice == "2":
        user_input = input("Enter Morse (use 1-5 to assign fingers): ")
        play_raw_morse(user_input)

    elif choice == "3":
        filename = input("Enter filename: ")
        if os.path.exists(filename):
            with open(filename, 'r') as f:
                content = f.read().strip()
                if all(c in ".- / \n\r12345" for c in content):
                    play_raw_morse(content)
                else:
                    play_parsed_text(content)
        else:
            print("File not found!")
