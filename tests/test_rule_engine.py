from app.classifier.rule_engine import RuleEngine

def engine():
    return RuleEngine("config/keywords.yaml", threshold=55, high_threshold=85)

def test_discord_bot_task():
    r = engine().classify("Need someone to build a Discord bot for $200.")
    assert r.accepted
    assert r.category == "discord_bot"

def test_chrome_extension():
    r = engine().classify("Looking for a developer to make a Chrome extension.")
    assert r.accepted
    assert r.category == "browser_extension"

def test_python_automation():
    r = engine().classify("Can someone build a Python automation script?")
    assert r.accepted

def test_react_paid_project():
    r = engine().classify("Paid project, need a React developer.")
    assert r.accepted

def test_collaboration_rejected():
    r = engine().classify("I am looking for a long-term collaboration partner based in US, UK, CA.")
    assert not r.accepted

def test_cofounder_rejected():
    r = engine().classify("Looking for a technical cofounder.")
    assert not r.accepted

def test_trading_opportunity_rejected():
    r = engine().classify("Looking for people interested in crypto trading. No experience required.")
    assert not r.accepted

def test_translation_rejected():
    r = engine().classify("Need translator for a document.")
    assert not r.accepted

def test_bidder_rejected():
    r = engine().classify("Looking for a bidder to bid on projects.")
    assert not r.accepted

def test_tutorial_rejected():
    r = engine().classify("Anyone know a good Python tutorial?")
    assert not r.accepted

def test_legitimate_trading_dashboard():
    r = engine().classify("I need a developer to build a React trading dashboard.")
    assert r.accepted

def test_dropshipping_rejected():
    r = engine().classify("Dropshipping opportunity, make money from home.")
    assert not r.accepted

def test_bot_creation():
    r = engine().classify("Can someone create a Telegram bot for my business?")
    assert r.accepted

def test_stripe_integration():
    r = engine().classify("Need someone to integrate Stripe into my website.")
    assert r.accepted
