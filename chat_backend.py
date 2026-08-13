"""
Chatbot B4AE — Backend
------------------------------------------------
Reçoit les messages des visiteurs (via le widget sur le site) et
repond automatiquement en utilisant le systeme multi-IA deja
configure (Claude -> Kimi -> Grok). Remplace le tri manuel WhatsApp.
"""

import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

STRIPE_LINK = "https://buy.stripe.com/00w00lez020ZbOjgpScAo01"
CONTACT_EMAIL = "contact@segplaza.com"

SYSTEM_CONTEXT = {
    "role_data": {
        "business_name": "B4AE Network",
        "what_we_do": (
            "B4AE est un reseau d'intelligence de marche pour l'electronique "
            "et le sourcing en Chine, base a Shenzhen. Nous aidons les "
            "entreprises a trouver des produits, fournisseurs, et prix "
            "reels sur les marches electroniques de Huaqiangbei et ailleurs."
        ),
        "subscription_model": (
            f"Acces a la base de donnees B4AE et soumission de demandes "
            f"de sourcing : 20 USD/mois. Lien d'inscription : {STRIPE_LINK}"
        ),
        "contact_email": CONTACT_EMAIL,
        "rules": [
            "Ne jamais donner de prix precis sans que la personne soit abonnee",
            "Ne jamais inventer d'informations sur des produits ou fournisseurs specifiques",
            "Pour toute demande serieuse de sourcing, rediriger vers le lien d'abonnement",
            "Rester chaleureux et professionnel, mais ne pas perdre de temps "
            "avec des demandes de prix isolees sans engagement",
            "Si la personne a deja un compte actif, la rediriger vers le formulaire de demande",
        ],
    }
}


def call_llm_simple(user_message, history):
    """Version simplifiee autonome pour Render - appelle Claude directement."""
    import requests

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return "Configuration manquante. Contactez-nous : " + CONTACT_EMAIL

    system_prompt = (
        "Tu es l'assistant de chat automatise de B4AE Network. "
        f"Contexte business : {json.dumps(SYSTEM_CONTEXT, ensure_ascii=False)}\n\n"
        "Reponds directement et naturellement, en respectant strictement "
        "les regles fournies. Reponds en 2-4 phrases maximum, ton "
        "conversationnel et chaleureux. Reponds dans la MEME LANGUE que "
        "le message du visiteur (francais, anglais, ou chinois)."
    )

    messages = history + [{"role": "user", "content": user_message}]

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 500,
        "system": system_prompt,
        "messages": messages,
    }

    resp = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json()["content"][0]["text"].strip()


@app.route("/chat", methods=["POST"])
def chat():
    body = request.get_json()
    user_message = body.get("message", "").strip()
    history = body.get("history", [])

    if not user_message:
        return jsonify({"error": "message vide"}), 400

    try:
        reply = call_llm_simple(user_message, history)
    except Exception as e:
        reply = f"Désolé, une erreur technique est survenue. Contactez-nous : {CONTACT_EMAIL}"
        print(f"Erreur chatbot : {e}")

    return jsonify({"reply": reply})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
