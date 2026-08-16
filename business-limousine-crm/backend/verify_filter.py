"""
verify_filter.py
Script de diagnostic à lancer une fois depuis backend/ après avoir installé
email_filter.py et remplacé database.py / models.py / email_fetcher.py.

Il fait 3 choses :
1. Vérifie que les tables blocked_senders / allowed_senders existent bien
   (les crée si besoin, sans toucher au reste de la base).
2. Peuple la blacklist avec les expéditeurs de bruit déjà repérés dans vos
   captures d'écran (TikTok, waynium.net backup, Doccle, ANAC Carwash...).
3. Rejoue le filtre sur ces mêmes adresses et affiche si Gemini aurait été
   appelé ou non — sans toucher à votre boîte mail ni consommer de quota API.

Lancer avec :  python verify_filter.py
"""
import sys

from database import db_session, init_db
import models
from email_filter import pre_filter

# Expéditeurs bruyants identifiés dans vos échanges précédents.
SEED_BLOCKLIST = [
    ("waynium.net", "Notifications de sauvegarde WordPress"),
    ("doccle.be", "Notifications Doccle (pas un client)"),
    ("anaccarwash.com", "Notifications ANAC Carwash (pas un client)"),
    ("service.tiktok.com", "Notifications TikTok"),
    ("info@business-limousine.be", "Notifications WordPress internes (modération commentaires)"),
    ("admin@business-limousine.be", "Notifications WordPress internes (admin)"),
]

# Emails de test pour rejouer le filtre après le seed.
TEST_CASES = [
    ("backup@waynium.net", "Sauvegarde/Backup LIMO - mission"),
    ("notification@service.tiktok.com", "Une nouvelle notification t'attend"),
    ("community@doccle.be", "LASAAD BEJAOUI a partagé des fi..."),
    ("info@anaccarwash.com", "Notification du renouvellement"),
    ("adele.agache@ef.com", "AVENUE LOUISE 279, réservation transfert"),  # doit passer par l'IA
]


def main():
    print("1. Vérification des tables...")
    init_db()
    print("   OK — blocked_senders / allowed_senders existent (ou ont été créées).\n")

    print("2. Peuplement de la blacklist...")
    with db_session() as conn:
        for pattern, reason in SEED_BLOCKLIST:
            models.add_blocked_sender(conn, pattern, reason=reason)
            print(f"   + {pattern}  ({reason})")
    print()

    print("3. Rejeu du filtre sur des cas réels :\n")
    with db_session() as conn:
        for from_addr, subject in TEST_CASES:
            result = pre_filter(conn, from_addr=from_addr, subject=subject, body_text="")
            if result is None:
                print(f"   [AI APPELÉE]     {from_addr:35s} — passerait par Gemini")
            else:
                print(f"   [FILTRÉ, 0 coût]  {from_addr:35s} — {result['summary']}")

    print("\n4. État actuel de la blacklist (hit_count = nb de fois où elle a matché) :")
    with db_session() as conn:
        for row in models.list_blocked_senders(conn):
            print(f"   {row['pattern']:35s} hits={row['hit_count']:<3d} last_hit={row['last_hit_at']}")


if __name__ == "__main__":
    main()