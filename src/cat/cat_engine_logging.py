import csv
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import sessionmaker

import config
from src.cat.db_connector import engine
from src.models.sqlalchemy_models import LastLogDate, QuizLog, QuestionLog


# class for cat_engine logging
class CELog:
    def __init__(self, quiz_id=None, log_time=None, log_id=None, question_id=None, quiz_start_time=None,
                 question_start_time=None, quiz_end_time=None, answer=None, start_difficulty=None, end_difficulty=None,
                 denominator=None, update_rate=None, student_score=None):
        self.log_id = log_id
        self.log_time = log_time
        self.quiz_id = quiz_id
        self.question_id = question_id
        self.quiz_start_time = quiz_start_time
        self.question_start_time = question_start_time
        self.quiz_end_time = quiz_end_time
        self.answer = answer
        self.start_difficulty = start_difficulty
        self.end_difficulty = end_difficulty
        self.denominator = denominator
        self.update_rate = update_rate
        self.student_score = student_score

    def get_log_representation(self):
        delimiter = config.log_settings["csv_delimiter"]
        return delimiter.join([
            str(self.quiz_id),
            str(self.question_id),
            str(self.quiz_start_time),
            str(self.question_start_time),
            str(self.quiz_end_time),
            str(self.answer),
            str(self.start_difficulty),
            str(self.end_difficulty),
            str(self.denominator),
            str(self.update_rate),
            str(self.student_score)
        ])


# inserts logs from logfile into db and returns date of last log inserted
def log_to_database():
    log_file = Path("../cat-module/logfiles/ce_logfile.csv")
    with open(log_file, "r", encoding='utf-8') as csv_file:
        csv_reader = csv.DictReader(csv_file, delimiter=config.log_settings["csv_delimiter"])

        session = sessionmaker(bind=engine)()

        # get the last date logs were inserted into the db
        last_db_log_date = session.query(LastLogDate).first().date

        # iterate through logfile lines
        for line in csv_reader:
            file_log_date = line["log_time"]
            # compare last db log (insertion) date against log time for logfile line:
            # skips logfile line if line has already been inserted into db
            if datetime.strptime(file_log_date, "%Y-%m-%d %H:%M:%S.%f") <= last_db_log_date:
                continue

            ce_log = CELog(
                log_time=file_log_date,
                quiz_id=line["quiz_id"],
                question_id=line["question_id"],
                quiz_start_time=line["quiz_start_time"],
                question_start_time=line["question_start_time"],
                quiz_end_time=line["quiz_end_time"],
                answer=line["answer"],
                start_difficulty=line["start_difficulty"],
                end_difficulty=line["end_difficulty"],
                denominator=line["denominator"],
                update_rate=line["update_rate"],
                student_score=line["student_score"]
            )

            # (usual logging order: quiz_start_time > question_start_time > rest of question > quiz_end_time)

            # check which "type" of logfile line it is
            if ce_log.quiz_start_time != "None":  # if log with quiz_start_time is encountered
                # insert new quiz log entry with only quiz_id and quiz_start_time (quiz_end_time is None/0)
                session.add(QuizLog(
                    quiz_id=ce_log.quiz_id,
                    quiz_start_time=ce_log.quiz_start_time,
                    quiz_end_time=ce_log.quiz_end_time
                ))
            elif ce_log.question_start_time != "None":  # if log with question_start_time is encountered
                # insert new question log entry with only log_time, quiz_id, question_id
                # and question_start_time (the rest is None/0)
                session.add(QuestionLog(
                    log_time=ce_log.log_time,
                    quiz_id=ce_log.quiz_id,
                    question_id=ce_log.question_id,
                    question_start_time=ce_log.question_start_time,
                    answer=ce_log.answer,
                    start_difficulty=ce_log.start_difficulty,
                    end_difficulty=ce_log.end_difficulty,
                    denominator=ce_log.denominator,
                    update_rate=ce_log.update_rate,
                    student_score=ce_log.student_score
                ))
            elif ce_log.quiz_end_time != "None":  # if log with quiz_end_time is encountered
                # update already existing quiz log with quiz end time
                session.query(QuizLog) \
                    .filter(QuizLog.quiz_id == ce_log.quiz_id) \
                    .update({"quiz_end_time": ce_log.quiz_end_time}, synchronize_session="fetch")
            else:  # any other log, these logs contain the rest of question log details
                # update already existing question log with remaining question log details
                session.query(QuestionLog) \
                    .filter(QuestionLog.question_id == ce_log.question_id) \
                    .update({
                        "answer": ce_log.answer,
                        "start_difficulty": ce_log.start_difficulty,
                        "end_difficulty": ce_log.end_difficulty,
                        "denominator": ce_log.denominator,
                        "update_rate": ce_log.update_rate,
                        "student_score": ce_log.student_score
                    }, synchronize_session="fetch")
                last_db_log_date = datetime.strptime(file_log_date, "%Y-%m-%d %H:%M:%S.%f")
        # delete last db log (insertion) date
        old_last_db_log_date = session.query(LastLogDate).first()
        session.delete(old_last_db_log_date)
        # add new db log (insertion) date
        session.add(LastLogDate(date=last_db_log_date))
        # commit all changes
        session.commit()
        return last_db_log_date
