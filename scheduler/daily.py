from apscheduler.schedulers.blocking import BlockingScheduler
from src.ingestion.run import update
from src.config import UPDATE_HOUR


def main():
    sched = BlockingScheduler()
    sched.add_job(lambda: update(days=1), "cron", hour=UPDATE_HOUR, minute=0)
    # primeira execução imediata
    print(update(days=7))
    sched.start()


if __name__ == "__main__":
    main()
