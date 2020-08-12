# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
import os

from datetime import datetime
from pathlib import Path
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from github import GithubIntegration

try:
    from flask_restx import Api, Resource
except ModuleNotFoundError:
    from flask_restplus import Api, Resource


api = Api()
db = SQLAlchemy()
github_integration = None
token_renew_at = None


def create_app():
    app = Flask(__name__)
    app.config.from_envvar("TOKMAN_CONFIG")

    log_level = os.getenv("LOG_LEVEL", "info")
    log_level = getattr(logging, log_level.upper())
    logging.basicConfig(level=log_level)

    private_key = Path(app.config["GITHUB_APP_PRIVATE_KEY"]).read_text()
    app_id = int(app.config["GITHUB_APP_ID"])
    global github_integration
    github_integration = GithubIntegration(app_id, private_key)
    global token_renew_at
    token_renew_at = int(app.config.get("TOKEN_RENEW_AT", 60))

    api.init_app(app)
    db.init_app(app)

    return app


class Token(db.Model):
    __tablename__ = "tokens"
    id = db.Column(db.Integer, primary_key=True)
    namespace = db.Column(db.String(), unique=True, nullable=False)
    repository = db.Column(db.String(), unique=True, nullable=False)
    token = db.Column(db.String(), unique=True, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)

    def is_expired(self):
        return (
            self.expires_at is None
            or self.token is None
            or (self.expires_at - datetime.utcnow()).seconds < token_renew_at
        )


class AppNotInstalledError(Exception):
    pass


def get_token(namespace, repository):
    inst_id = github_integration.get_installation(namespace, repository).id
    inst_id = inst_id if isinstance(inst_id, int) or inst_id is None else inst_id.value
    if not inst_id:
        raise AppNotInstalledError(f"App is not installed on {namespace}/{repository}")
    inst_auth = github_integration.get_access_token(inst_id)
    # expires_at is UTC
    return inst_auth.token, inst_auth.expires_at


@api.route("/api/<string:namespace>/<string:repository>")
@api.param("namespace", "GitHub namespace")
@api.param("repository", "GitHub repository")
class AccessToken(Resource):
    def get(self, namespace: str, repository: str):
        """Return an access token for <namespace>/<repository>"""
        token = Token.query.filter_by(
            namespace=namespace, repository=repository
        ).first()
        if token is None:
            token = Token(namespace=namespace, repository=repository)
            db.session.add(token)
            api.logger.debug(f"Add {namespace}/{repository} to the DB")

        if token.is_expired():
            try:
                token.token, token.expires_at = get_token(namespace, repository)
                api.logger.debug(f"Get token for {namespace}/{repository}")
            except AppNotInstalledError as err:
                api.logger.debug(f"Failed to get token for {namespace}/{repository}")
                return {"error": f"Failed to retrieve a token: {err}"}, 400

        db.session.commit()
        api.logger.debug("Commit to DB")

        return {
            "repository": repository,
            "namespace": namespace,
            "access_token": token.token,
        }


@api.route("/api/health")
class Health(Resource):
    def get(self):
        """Is this up and running?"""
        return {"message": "ok"}

    def head(self):
        pass
