"""
B4AE Access Backend — Checkout multi-categories + Webhook + API protegee
--------------------------------------------------------------------------
3 endpoints :
    POST /create-checkout   -> le client choisit ses categories, reçoit
                                une URL Stripe Checkout a payer
    POST /stripe-webhook    -> Stripe appelle ici apres paiement reussi,
                                on accorde l'acces dans Supabase
    GET  /api/category-data/<category>  -> retourne les donnees SEULEMENT
                                si l'usager a un abonnement actif

Necessite dans ton .env (et sur Render en variables d'environnement) :
    STRIPE_SECRET_KEY
    STRIPE_WEBHOOK_SECRET
    SUPABASE_URL_B4AE
    SUPABASE_SERVICE_KEY_B4AE

Installation :
    pip install flask flask-cors stripe requests python-dotenv --break-system-packages
"""

import os
import json
import requests
import stripe
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv(".env")

stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

SUPABASE_URL = os.environ["SUPABASE_URL_B4AE"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY_B4AE"]

SUCCESS_URL = os.environ.get("CHECKOUT_SUCCESS_URL", "https://b4ae.com/checkout-success")
CANCEL_URL = os.environ.get("CHECKOUT_CANCEL_URL", "https://b4ae.com/checkout-cancelled")

CATEGORY_PRICES = {
    "phone": {"market_reference": 2000, "verified_live": 3500},
}

app = Flask(__name__)
CORS(app)

SUPABASE_HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
}


def get_or_create_supabase_user(email):
    list_url = f"{SUPABASE_URL}/auth/v1/admin/users"
    resp = requests.get(list_url, headers=SUPABASE_HEADERS, params={"email": email})
    if resp.status_code == 200:
        users = resp.json().get("users", [])
        for u in users:
            if u.get("email") == email:
                return u["id"]

    create_resp = requests.post(
        f"{SUPABASE_URL}/auth/v1/admin/users",
        headers=SUPABASE_HEADERS,
        json={"email": email, "email_confirm": True},
    )
    if create_resp.status_code in (200, 201):
        return create_resp.json()["id"]
    return None


def grant_access(user_id, category, tier, stripe_customer_id, stripe_subscription_id):
    url = f"{SUPABASE_URL}/rest/v1/user_category_access"
    payload = {
        "user_id": user_id,
        "category": category,
        "tier": tier,
        "stripe_customer_id": stripe_customer_id,
        "stripe_subscription_id": stripe_subscription_id,
        "status": "active",
    }
    headers = {**SUPABASE_HEADERS, "Prefer": "resolution=merge-duplicates"}
    resp = requests.post(url, headers=headers, json=payload)
    return resp.status_code in (200, 201)


def check_access(user_id, category):
    url = f"{SUPABASE_URL}/rest/v1/user_category_access"
    params = {
        "user_id": f"eq.{user_id}",
        "category": f"eq.{category}",
        "status": "eq.active",
    }
    resp = requests.get(url, headers=SUPABASE_HEADERS, params=params)
    if resp.status_code == 200 and len(resp.json()) > 0:
        return True
    return False


@app.route("/create-checkout", methods=["POST"])
def create_checkout():
    body = request.get_json()
    email = body.get("email")
    categories = body.get("categories", [])

    if not email or not categories:
        return jsonify({"error": "email et categories requis"}), 400

    line_items = []
    for c in categories:
        cat = c["category"]
        tier = c["tier"]
        price_cents = CATEGORY_PRICES.get(cat, {}).get(tier)
        if not price_cents:
            return jsonify({"error": f"Prix inconnu pour {cat}/{tier}"}), 400

        line_items.append({
            "price_data": {
                "currency": "usd",
                "product_data": {
                    "name": f"B4AE {cat.title()} - {tier.replace('_', ' ').title()}",
                },
                "unit_amount": price_cents,
                "recurring": {"interval": "month"},
            },
            "quantity": 1,
        })

    try:
        session = stripe.checkout.Session.create(
            customer_email=email,
            payment_method_types=["card"],
            line_items=line_items,
            mode="subscription",
            success_url=SUCCESS_URL,
            cancel_url=CANCEL_URL,
            metadata={"categories_json": json.dumps(categories)},
        )
        return jsonify({"checkout_url": session.url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except Exception as e:
        print(f"Erreur signature webhook : {e}")
        return jsonify({"error": "invalid signature"}), 400

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        email = session.get("customer_email") or session.get("customer_details", {}).get("email")
        stripe_customer_id = session.get("customer")
        stripe_subscription_id = session.get("subscription")

        categories_json = session.get("metadata", {}).get("categories_json", "[]")
        categories = json.loads(categories_json)

        user_id = get_or_create_supabase_user(email)
        if not user_id:
            print(f"Impossible de creer/trouver l'usager Supabase pour {email}")
            return jsonify({"received": True}), 200

        for c in categories:
            success = grant_access(
                user_id, c["category"], c["tier"], stripe_customer_id, stripe_subscription_id
            )
            print(f"Acces {'accorde' if success else 'ECHEC'} : {email} -> {c['category']}/{c['tier']}")

    return jsonify({"received": True}), 200


@app.route("/api/category-data/<category>", methods=["GET"])
def get_category_data(category):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "Non authentifie"}), 401

    token = auth_header.replace("Bearer ", "")

    user_resp = requests.get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {token}"},
    )
    if user_resp.status_code != 200:
        return jsonify({"error": "Token invalide"}), 401

    user_id = user_resp.json().get("id")

    if not check_access(user_id, category):
        return jsonify({
            "error": "Acces non autorise",
            "message": f"Abonnez-vous a la categorie '{category}' pour voir ces donnees.",
            "subscribe_url": "https://b4ae.com/subscribe"
        }), 402

    table_name = f"{category}_live_market"
    data_resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table_name}",
        headers=SUPABASE_HEADERS,
        params={"select": "*", "status": "eq.Live"},
    )
    if data_resp.status_code == 200:
        return jsonify(data_resp.json())
    return jsonify({"error": "Erreur recuperation donnees"}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    app.run(host="0.0.0.0", port=port, debug=False)
