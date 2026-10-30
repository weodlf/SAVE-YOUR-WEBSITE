from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

def now():
    return datetime.now(timezone.utc).isoformat()

class SecurityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.String(40), default=now, index=True)
    ip = db.Column(db.String(45), nullable=False)
    kind = db.Column(db.String(64), nullable=False)
    severity = db.Column(db.String(16), nullable=False)
    message = db.Column(db.Text, nullable=False)
    simulated = db.Column(db.Boolean, default=False, nullable=False)

class BlockedIP(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(45), unique=True, nullable=False)
    timestamp = db.Column(db.String(40), default=now)
    reason = db.Column(db.String(128), nullable=False)
    os_status = db.Column(db.String(512), nullable=False, default='disabled')

class ScanHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    target = db.Column(db.String(256), nullable=False)
    timestamp = db.Column(db.String(40), default=now)
    status = db.Column(db.String(32), default='queued')
    progress = db.Column(db.Integer, default=0)
    results = db.Column(db.JSON, default=dict)

def serialize(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}
