from sqlalchemy import Column, Integer, ForeignKey, Float, String, Date, Boolean, DECIMAL, DateTime, BigInteger
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.dialects.mysql import DATETIME

Base = declarative_base()  # used for ORM


# Question class for ORM of Questions table
# TODO: For now discrimination, pseudoGuessing and upperAsymptote are not considered.
# In the old data model, questions and topics are in a many-to-many association, with difficulty being in the
# association class, but the above attributes are not. Shouldn't they also be in the association class?
class Question(Base):
    __tablename__ = "Questions"

    question_id = Column(Integer, primary_key=True)
    material_id = Column(String(8), unique=True, nullable=False)
    last_modified = Column(Date, nullable=True)
    is_active = Column(Boolean, nullable=True)
    # exact data types to be determined:
    discrimination = Column(Float, nullable=True)
    pseudo_guessing = Column(Float, nullable=True)
    upper_asymptote = Column(Float, nullable=True)

    difficulty = relationship("Difficulty", back_populates="question")


# Difficulty class for ORM of Difficulties table
class Difficulty(Base):
    __tablename__ = "Difficulties"

    question_id = Column(Integer, ForeignKey("Questions.question_id"), primary_key=True)
    topic_id = Column(String(255), primary_key=True)
    difficulty = Column(DECIMAL(11, 10), nullable=False)

    question = relationship("Question", back_populates="difficulty")


class QuestionLog(Base):
    __tablename__ = "QuestionLogs"

    # log_id will not be returned when log representation is called
    log_id = Column(Integer, primary_key=True)
    log_time = Column(DateTime, nullable=True)
    quiz_id = Column(BigInteger, ForeignKey("QuizLogs.quiz_id"), nullable=True)
    question_id = Column(Integer, nullable=True)
    question_start_time = Column(DateTime, nullable=True)
    answer = Column(Integer, nullable=True)
    start_difficulty = Column(DECIMAL(11, 10), nullable=True)
    end_difficulty = Column(DECIMAL(11, 10), nullable=True)
    denominator = Column(Integer, nullable=True)
    update_rate = Column(DECIMAL(3, 2), nullable=True)
    student_score = Column(DECIMAL(6, 4), nullable=True)

    quiz_log = relationship("QuizLog", back_populates="question_log")


class QuizLog(Base):
    __tablename__ = "QuizLogs"

    quiz_id = Column(BigInteger, primary_key=True, autoincrement=False)
    quiz_start_time = Column(DateTime, nullable=False)  # quiz always has a start time
    quiz_end_time = Column(DateTime, nullable=True)  # but not always an end time (if quiz unfinished)

    question_log = relationship("QuestionLog", back_populates="quiz_log")


class LastLogDate(Base):
    __tablename__ = "LastLogDate"  # singular, as there will be always one single date

    date = Column(DATETIME(fsp=3), primary_key=True, autoincrement=False)