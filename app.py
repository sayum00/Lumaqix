from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# Dummy database
users_db = {}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/user', methods=['POST'])
def get_or_create_user():
    data = request.json
    user_id = str(data.get('telegram_id'))
    username = data.get('username', 'User')
    
    if user_id not in users_db:
        users_db[user_id] = {
            "username": username,
            "balance": 100,
            "referrals": 0,
            "ref_link": f"https://t.me/your_bot_username?start=ref_{user_id}"
        }
    
    return jsonify(users_db[user_id])

@app.route('/api/claim', methods=['POST'])
def claim_reward():
    data = request.json
    user_id = str(data.get('telegram_id'))
    
    if user_id in users_db:
        users_db[user_id]["balance"] += 10
        return jsonify({"success": True, "new_balance": users_db[user_id]["balance"]})
    
    return jsonify({"success": False})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
