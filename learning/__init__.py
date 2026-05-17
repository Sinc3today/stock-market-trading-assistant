"""
learning/ — Self-learning subsystem for the SPY trading assistant.

Modules:
    knowledge_base       Append-only KB of structured observations
    paper_broker         Auto-records paper trades from the daily play
    outcome_resolver     Closes paper trades and scores predictions at EOD
    reflector            Daily Claude reflection -> KB + markdown
    hypothesis_engine    Weekly Claude proposal of one tunable change
    hypothesis_runner    Runs the proposed change through the backtest
    off_hours_learner    Weekend learning when market is closed
    scheduler            Registers all learning jobs onto the main APScheduler

The loop:
    09:15 ET  paper_broker.execute_today()       (from spy daily premarket job)
    16:05 ET  outcome_resolver.resolve_today()   (new job)
    19:00 ET  reflector.reflect_today()          (replaces dumb prompt)
    Sat 10:00 hypothesis_engine.propose_weekly()
    Sat 11:00 hypothesis_runner.run_pending()
    Sun 10:00 off_hours_learner.run()
"""

from learning.knowledge_base import KnowledgeBase, KBEntry  # noqa: F401
