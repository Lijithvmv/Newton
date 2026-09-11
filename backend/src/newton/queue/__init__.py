"""Job queue — run many goals unattended, one at a time, checkpointed so the whole batch resumes.

This is the "flight story" layer: queue a list of jobs (a JSONL/text file, one goal per line),
process them one at a time on localhost through the loop engine, save each result, and checkpoint
after every job — so a long, offline run that gets interrupted resumes exactly where it stopped,
never repeating finished work. Durable execution at the batch grain, the way the loop engine is
durable at the step grain.
"""

from .runner import JobQueue, QueueResult
from .state import DONE, FAILED, PENDING, RUNNING, Job, QueueState

__all__ = ["JobQueue", "QueueResult", "QueueState", "Job", "PENDING", "RUNNING", "DONE", "FAILED"]
