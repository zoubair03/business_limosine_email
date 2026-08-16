"""
Authentication module for Business Limousine CRM.

Provides secure password hashing, session management, and access-control decorators.
"""
from functools import wraps
from flask import g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

import models
from database import db_session


def hash_password(password: str) -> str:
    """Securely hashes a plaintext password using modern scrypt/pbkdf2."""
    if not password:
        raise ValueError("Password cannot be empty")
    try:
        return generate_password_hash(password, method="scrypt")
    except Exception:
        return generate_password_hash(password, method="pbkdf2:sha256")


def verify_password(password_hash: str, password: str) -> bool:
    """Verifies a plaintext password against a stored hash."""
    if not password_hash or not password:
        return False
    return check_password_hash(password_hash, password)


def login_user(user: dict):
    """Establishes an authenticated session for the given user."""
    session.permanent = True
    session["user_id"] = user["id"]
    session["email"] = user["email"]
    session["full_name"] = user["full_name"]
    session["role"] = user["role"]
    session["avatar_color"] = user.get("avatar_color", "#C5A059")


def logout_user():
    """Clears the authenticated user session."""
    session.clear()


def get_current_user():
    """
    Returns the currently authenticated user dictionary, or None if not logged in.
    Caches the user on Flask's `g` object for the duration of the request.
    """
    if hasattr(g, "_current_user"):
        return g._current_user

    user_id = session.get("user_id")
    if not user_id:
        g._current_user = None
        return None

    with db_session() as conn:
        user = models.get_user_by_id(conn, user_id)
        if not user or not user["is_active"]:
            session.clear()
            g._current_user = None
            return None

        user_dict = {
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user["role"],
            "avatar_color": user["avatar_color"],
            "is_active": bool(user["is_active"]),
            "created_at": user["created_at"],
        }
        g._current_user = user_dict
        return user_dict


def login_required(f):
    """Decorator to require an authenticated session for an API route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({"error": "Authentication required", "code": "UNAUTHORIZED"}), 401
        return f(*args, **kwargs)
    return decorated_function


def roles_required(*allowed_roles):
    """Decorator to restrict access to users with specific roles (e.g. 'ADMIN')."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify({"error": "Authentication required", "code": "UNAUTHORIZED"}), 401
            if user.get("role") not in allowed_roles:
                return jsonify({
                    "error": f"Forbidden: Requires one of roles {allowed_roles}",
                    "code": "FORBIDDEN"
                }), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator
