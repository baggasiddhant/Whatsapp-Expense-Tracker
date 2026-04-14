from flask import Flask, request, jsonify
import re
import dateparser
from transformers import AutoModelForCausalLM, AutoTokenizer
from twilio.rest import Client
import sqlite3
import openai
import os

# Initialize Flask app
app = Flask(__name__)

# OpenAI API key setup
openai.api_key = os.getenv('Hidden Api Key')

# Initialize the model and tokenizer (optional)
model_name = "distilgpt2"
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForCausalLM.from_pretrained(model_name)

# Connect to the SQLite database
conn = sqlite3.connect('expenses.db', check_same_thread=False)
cursor = conn.cursor()

# Create table if not exists
cursor.execute('''
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amount INTEGER,
    item TEXT,
    category TEXT,
    date TEXT
)
''')
conn.commit()

# Function to generate a response using OpenAI's GPT-3.5/GPT-4
def generate_model_response(prompt):
    response = openai.Completion.create(
        engine="gpt-3.5-turbo",  # or "gpt-4"
        prompt=prompt,
        max_tokens=100
    )
    return response.choices[0].text.strip()

# Function to classify user intent
def classify_intent(user_input):
    user_input_lower = user_input.lower()
    expense_keywords = ["spent", "paid", "cost", "bought", "ordered", "₹", "rs", "rupees"]
    if any(word in user_input_lower for word in expense_keywords):
        return "add_expense"
    elif any(word in user_input_lower for word in ["query", "how much", "total", "list"]):
        return "query_spending"
    elif "get balance" in user_input_lower:
        return "get_balance"
    elif any(word in user_input_lower for word in ["category breakdown", "breakdown"]):
        return "category_breakdown"
    else:
        return "unknown"

# Function to parse expense message
def parse_expense_message(message):
    amount_match = re.search(r'(?:₹|Rs|Rupees)\s?(\d+(?:\.\d{1,2})?)', message, re.IGNORECASE)
    if not amount_match:
        amount_match = re.search(r'(\d+(?:\.\d{1,2})?)\s?(?:₹|Rs|Rupees)', message, re.IGNORECASE)
    amount = int(float(amount_match.group(1))) if amount_match else None

    item_patterns = [
        r'(?:for|on)\s(.+?)(?:at|from|₹|Rs|Rupees|$)',
        r'(?:bought|ordered)\s(.+?)(?:for|at|from|₹|Rs|Rupees|$)',
        r'(.+?)\s(?:cost|for|at|from|₹|Rs|Rupees)'
    ]
    item = None
    for pattern in item_patterns:
        item_match = re.search(pattern, message, re.IGNORECASE)
        if item_match:
            item = item_match.group(1).strip()
            break

    date_keywords = ['yesterday', 'today', 'this morning', 'last night', 'this evening']
    date_str = next((keyword for keyword in date_keywords if keyword in message.lower()), None)
    if not date_str:
        date_match = re.search(r'on (.+)', message)
        date_str = date_match.group(1) if date_match else None
    date = dateparser.parse(date_str) if date_str else None

    return {
        'amount': amount,
        'item': item,
        'date': date.strftime('%Y-%m-%d') if date else None
    }

# Function to categorize expense
def categorize_expense(item):
    if not item:
        return 'Other'
    item_lower = item.lower()
    categories = {
        'Food': ['restaurant', 'groceries', 'food', 'lunch', 'dinner', 'breakfast', 'chai', 'tea', 'coffee', 'dosa', 'burger', 'pizza', 'biryani', 'ice cream'],
        'Travel': ['flight', 'hotel', 'ride', 'uber', 'ola', 'auto', 'metro', 'taxi', 'cab'],
        'Shopping': ['shoes', 'clothing', 'clothes', 'bata'],
        'Online Orders': ['swiggy', 'blinkit', 'zepto', 'online', 'order', 'delivery'],
        'Entertainment': ['movie', 'ticket', 'cinema', 'theatre'],
        'Transportation': ['bike', 'car', 'servicing', 'fuel', 'petrol', 'diesel'],
        'Utilities': ['mobile', 'recharge', 'bill', 'electricity', 'water', 'gas']
    }
    for category, keywords in categories.items():
        if any(keyword in item_lower for keyword in keywords):
            return category
    return 'Other'

# Function to add expense to the database
def add_expense_to_db(amount, item, category, date):
    cursor.execute('''
    INSERT INTO expenses (amount, item, category, date)
    VALUES (?, ?, ?, ?)
    ''', (amount, item, category, date))
    conn.commit()

# Function to handle different types of queries
def handle_query(user_input):
    user_input_lower = user_input.lower()

    categories = ["food", "travel", "shopping", "online orders", "entertainment", "transportation", "utilities"]
    for category in categories:
        if category in user_input_lower:
            return get_category_expenses(category.capitalize())

    if "total expenses" in user_input_lower:
        return get_total_expenses()

    if "category breakdown" in user_input_lower:
        return get_category_breakdown()

    return "I'm not sure what you mean. Could you please rephrase your query?"

def get_category_expenses(category):
    cursor.execute('''
    SELECT item, amount FROM expenses WHERE category = ?
    ''', (category,))
    expenses = cursor.fetchall()
    
    if not expenses:
        return f"No expenses found for category '{category}'."

    total = sum(expense[1] for expense in expenses)
    items_list = "\n".join([f"{expense[0]}: ₹{expense[1]}" for expense in expenses])
    return f"Here's a list of all your {category.lower()} expenses:\n{items_list}\nTotal {category.lower()} expenses: ₹{total}"

def get_total_expenses():
    cursor.execute('SELECT SUM(amount) FROM expenses')
    total = cursor.fetchone()[0] or 0
    return f"Your total expenses are ₹{total}."

def get_category_breakdown():
    cursor.execute('''
    SELECT category, SUM(amount) FROM expenses GROUP BY category
    ''')
    breakdown = cursor.fetchall()
    breakdown_list = "\n".join([f"{cat}: ₹{amount}" for cat, amount in breakdown])
    return f"Category breakdown:\n{breakdown_list}"

# Function to generate response based on intent
def generate_intent_response(user_input):
    intent = classify_intent(user_input)

    if intent == "add_expense":
        parsed_data = parse_expense_message(user_input)
        if not parsed_data['amount'] or not parsed_data['item']:
            return "Could not parse expense details. Please provide a complete expense description."
        category = categorize_expense(parsed_data['item'])
        add_expense_to_db(parsed_data['amount'], parsed_data['item'], category, parsed_data['date'])
        return f"Expense added! Amount: ₹{parsed_data['amount']}, Item: {parsed_data['item']}, Category: {category}"
    elif intent == "query_spending":
        return handle_query(user_input)
    elif intent == "get_balance":
        return generate_model_response("Your balance is ₹...")
    elif intent == "category_breakdown":
        return get_category_breakdown()
    else:
        return "I'm not sure what you mean. Could you please rephrase your request?"

# Function to send a message via WhatsApp using Twilio
def send_whatsapp_message(to, message):
    account_sid = os.getenv('ACbb1cb409056bd21b8ac4a91b6a0cb6a2')
    auth_token = os.getenv('17357ab33029f362ab9c73304827cc66')
    client = Client(account_sid, auth_token)

    message = client.messages.create(
        body=message,
        from_='whatsapp:+14155238886',  # Twilio's WhatsApp sandbox number
        to=f'whatsapp:{to}'
    )
    return message.sid

# Route to handle incoming messages from Twilio
@app.route('/webhook', methods=['POST'])
def webhook():
    from_number = request.form['From']
    incoming_msg = request.form['Body']
    
    response = generate_intent_response(incoming_msg)
    send_whatsapp_message(from_number, response)
    
    return jsonify({"status": "success"})

if __name__ == '__main__':
    app.run(debug=True)
