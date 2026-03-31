from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True, silent=True)
    print("Received:", data)

    if data and "challenge" in data:
        return jsonify({"challenge": data["challenge"]})

    return jsonify({"status": "received"}), 200

if __name__ == '__main__':
    app.run(port=3000, debug=True)
    
    
