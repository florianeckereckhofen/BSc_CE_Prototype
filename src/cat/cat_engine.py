import json
import random
import time
import urllib

from neo4j import GraphDatabase
from distutils.util import strtobool
from typing import List

import numpy as np
from catsim import irt

# estimation package contains different proficiency estimation methods
from catsim.estimation import DifferentialEvolutionEstimator

# initialization package contains different initial proficiency estimation strategies
from catsim.initialization import RandomInitializer, FixedPointInitializer

# selection package contains different item selection strategies
from catsim.selection import MaxInfoSelector, LinearSelector, UrrySelector

# stopping package contains different stopping criteria for the CAT
from catsim.stopping import MinErrorStopper, MaxItemStopper

from sqlalchemy.orm import sessionmaker
from sqlalchemy import func, update

import config
from src.cat.cat_engine_logging import CELog
from src.cat.db_connector import r, engine
# from src.cat.db_connector import *

from src.models.fastapi_models import QuizAPI, NextQuestionAPI, QuestionAPI, ResultAPI

import logging
from src.models.sqlalchemy_models import Difficulty

from datetime import datetime

# --------------- Setup logging ---------------

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

file_handler = logging.FileHandler(config.log_settings["ce_logfile"])
formatter = logging.Formatter('%(asctime)s.%(msecs)03d;%(message)s', '%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)


# --------------- Functionality ---------------

def create_quiz(quiz_api: QuizAPI):  # Save the quiz in Redis

    quiz_api.quizId = id(quiz_api)  # create unique quizID

    # log quiz_start_time
    log(CELog(
        quiz_id=quiz_api.quizId,
        quiz_start_time=datetime.now().strftime(config.log_settings["ce_time_format"])
    ))

    quiz_api.questions = get_questions_of_topic(quiz_api.topicId)

    # Save Data received from Call to Redis
    r.mset({get_r_prefix(quiz_api.quizId) + "maxNumberOfQuestions": quiz_api.maxNumberOfQuestions,
            get_r_prefix(quiz_api.quizId) + "minMeasurementAccuracy": quiz_api.minMeasurementAccuracy,
            get_r_prefix(quiz_api.quizId) + "inputProficiencyLevel": quiz_api.inputProficiencyLevel,
            get_r_prefix(quiz_api.quizId) + "questionSelector": quiz_api.questionSelector,
            get_r_prefix(quiz_api.quizId) + "competencyEstimator": quiz_api.competencyEstimator,
            get_r_prefix(quiz_api.quizId) + "topicId": quiz_api.topicId,
            get_r_prefix(quiz_api.quizId) + "standardErrorOfEstimation": config.defaultAdaptiveQuiz[
                "standardErrorOfEstimation"],
            get_r_prefix(quiz_api.quizId) + "quizFinished": str(False)
            })

    # TODO: (old) only getting topic id
    for question in quiz_api.questions:  # Store questions in Redis
        r.rpush(get_r_prefix(quiz_api.quizId) + "questions", json.dumps(question.__dict__))

    # Initialization Initializer (If InputProficiencyLevel is 99.9, a random difficulty will be chosen.)
    init_initializer(quiz_api)

    # Initialization DifferentialEvolutionEstimator
    init_estimator(quiz_api)

    # Selector specific initializations
    init_selector(quiz_api)

    r.rpush("quizIds", quiz_api.quizId)

    return quiz_api


# Calculate the next quiz question
def get_next_question(quiz_id: int, is_correct: float):
    # Load Questions
    items = get_items(quiz_id)
    administered_items = get_administered_items(quiz_id)

    # Define Stopping Criterion
    min_error_stopper = MinErrorStopper(float(r.get(get_r_prefix(
        quiz_id) + "minMeasurementAccuracy")))  # Describes the measurement accuracy threshold of the exam -->
    # standard error of estimation is used.
    max_item_stopper = MaxItemStopper(int(r.get(get_r_prefix(quiz_id) + "maxNumberOfQuestions")))

    selector = get_selector(quiz_id)

    if len(administered_items) == 0:  # Select first question and deliver it
        item_index = selector.select(items=get_items(quiz_id),  # maps cat-sim index to question id
                                     administered_items=administered_items,  # all answered questions
                                     est_theta=float(r.get(get_r_prefix(
                                         quiz_id) + "estTheta")))  # est_theta is the current compentce level
        r.set(get_r_prefix(quiz_id) + "itemIndex", int(item_index))

        next_question = NextQuestionAPI(quizId=quiz_id,
                                        questionId=get_question_id_by_index(quiz_id, item_index),
                                        materialId=get_material_id_by_index(quiz_id, item_index),
                                        measurementAccuracy=float(
                                            r.get(get_r_prefix(quiz_id) + "standardErrorOfEstimation")),
                                        currentCompetency=(float(r.get(get_r_prefix(quiz_id) + "estTheta"))),
                                        quizFinished=strtobool(
                                            r.get(get_r_prefix(quiz_id) + "quizFinished").decode()))
        r.rpush(get_r_prefix(quiz_id) + "administeredItems", int(item_index))

        # log quiz_id, question_id and question_start_time
        log(CELog(
            quiz_id=quiz_id,
            question_id=get_question_id_by_index(quiz_id, item_index),
            question_start_time=datetime.now().strftime(config.log_settings["ce_time_format"]),
        ))

    elif is_correct is not None and 0.0 <= is_correct <= 1.0:  # Check if input is okay -> TODO (old) move to API method and throw HTTPException if value is wrong
        r.rpush(get_r_prefix(quiz_id) + "responses", is_correct)  # Add response to List

        estimator = get_estimator(quiz_id)

        est_theta = estimator.estimate(items=items,
                                       administered_items=administered_items,
                                       response_vector=get_responses(quiz_id),
                                       est_theta=float(r.get(get_r_prefix(quiz_id) + "estTheta")))
        r.set(get_r_prefix(quiz_id) + "estTheta", est_theta)

        standard_error_of_estimation = irt.see(theta=est_theta, items=items[administered_items])
        r.set(get_r_prefix(quiz_id) + "standardErrorOfEstimation", standard_error_of_estimation)

        quiz_finished = (min_error_stopper.stop(administered_items=items[administered_items], theta=est_theta) or (
            max_item_stopper.stop(administered_items=items[administered_items])))
        r.set(get_r_prefix(quiz_id) + "quizFinished", str(quiz_finished))

        if not quiz_finished:
            item_index = selector.select(items=get_items(quiz_id),
                                         administered_items=administered_items,
                                         est_theta=est_theta)
            r.set(get_r_prefix(quiz_id) + "itemIndex", int(item_index))

            next_question = NextQuestionAPI(quizId=quiz_id,
                                            questionId=get_question_id_by_index(quiz_id, item_index),
                                            materialId=get_material_id_by_index(quiz_id, item_index),
                                            measurementAccuracy=standard_error_of_estimation,
                                            currentCompetency=est_theta,
                                            quizFinished=quiz_finished)
            r.rpush(get_r_prefix(quiz_id) + "administeredItems", int(item_index))

            # log quiz_id, question_id and question_start_time
            log(CELog(
                quiz_id=quiz_id,
                question_id=get_question_id_by_index(quiz_id, item_index),
                question_start_time=datetime.now().strftime(config.log_settings["ce_time_format"]),
            ))

        else:  # if quiz is already finished return no new questionId
            # log id and end time for quiz
            log(CELog(
                quiz_id=quiz_id,
                quiz_end_time=datetime.now().strftime(config.log_settings["ce_time_format"]),
            ))
            # calibrate difficulties of all administered questions
            calibrate_questions(quiz_id)
            # return question with questionId and materialId = None to signal end of quiz
            next_question = NextQuestionAPI(quizId=quiz_id,
                                            questionId=None,
                                            materialId=None,
                                            measurementAccuracy=standard_error_of_estimation,
                                            currentCompetency=est_theta,
                                            quizFinished=quiz_finished)
    return next_question


def get_result(quiz_id):
    # get the result of a non-adaptive quiz
    if r.get(get_r_prefix(quiz_id) + "questionSelector").decode(
            "utf-8") == 'linearSelector':
        responses = get_responses_as_float(quiz_id)
        r.set(get_r_prefix(quiz_id) + "standardErrorOfEstimation", 0.0)
        # needed to calculate percentage of correct answers
        achievable_points = 0.0
        achieved_points = 0.0
        i = 0
        administered_questions: List[QuestionAPI] = []  # create list of quiz questions with their real questionID.
        for item_index in get_administered_items(quiz_id):
            item = get_item_by_index(quiz_id, item_index)
            question_api = QuestionAPI(id=get_question_id_by_index(quiz_id, item_index),
                                       materialId=get_material_id_by_index(quiz_id, item_index),
                                       discrimination=item[0],
                                       difficulty=item[1], pseudoGuessing=item[2], upperAsymptote=item[3])
            administered_questions.append(question_api)
            achievable_points = achievable_points + item[1]
            achieved_points = achieved_points + item[1] * responses[i]
            i = i + 1
        r.set(get_r_prefix(quiz_id) + "estTheta", achieved_points / achievable_points)
        result = ResultAPI(quizId=quiz_id,
                           quizFinished=strtobool(r.get(get_r_prefix(quiz_id) + "quizFinished").decode()),
                           currentCompetency=float(r.get(get_r_prefix(quiz_id) + "estTheta")),
                           measurementAccuracy=float(
                               r.get(get_r_prefix(quiz_id) + "standardErrorOfEstimation")),
                           administeredQuestions=administered_questions,
                           responses=get_responses_as_float(quiz_id).tolist(),
                           maxNumberOfQuestions=int(r.get(get_r_prefix(quiz_id) + "maxNumberOfQuestions")))
    # get the result of an adaptive quiz
    else:
        administered_questions: List[QuestionAPI] = []  # create list of quiz questions with their real questionID.
        for item_index in get_administered_items(quiz_id):
            item = get_item_by_index(quiz_id, item_index)
            question_api = QuestionAPI(id=get_question_id_by_index(quiz_id, item_index),
                                       materialId=get_material_id_by_index(quiz_id, item_index),
                                       discrimination=item[0],
                                       difficulty=item[1], pseudoGuessing=item[2], upperAsymptote=item[3])
            administered_questions.append(question_api)
        result = ResultAPI(quizId=quiz_id,
                           quizFinished=strtobool(r.get(get_r_prefix(quiz_id) + "quizFinished").decode()),
                           currentCompetency=float(r.get(get_r_prefix(quiz_id) + "estTheta")),
                           measurementAccuracy=float(
                               r.get(get_r_prefix(quiz_id) + "standardErrorOfEstimation")),
                           administeredQuestions=administered_questions,
                           responses=get_responses_as_float(quiz_id).tolist(),
                           maxNumberOfQuestions=int(r.get(get_r_prefix(quiz_id) + "maxNumberOfQuestions")))
    return result


def delete_quiz(quiz_id_api):
    r.delete(get_r_prefix(quiz_id_api.quizId) + "maxNumberOfQuestions",
             get_r_prefix(quiz_id_api.quizId) + "minMeasurementAccuracy",
             get_r_prefix(quiz_id_api.quizId) + "inputProficiencyLevel",
             get_r_prefix(quiz_id_api.quizId) + "questionSelector",
             get_r_prefix(quiz_id_api.quizId) + "competencyEstimator",
             get_r_prefix(quiz_id_api.quizId) + "standardErrorOfEstimation",
             get_r_prefix(quiz_id_api.quizId) + "quizFinished",
             get_r_prefix(quiz_id_api.quizId) + "minDiff",
             get_r_prefix(quiz_id_api.quizId) + "maxDiff",
             get_r_prefix(quiz_id_api.quizId) + "questions",
             get_r_prefix(quiz_id_api.quizId) + "estTheta")
    r.lrem("quizIds", 0, quiz_id_api.quizId)


# --------------- Helper Methods ---------------

def get_r_prefix(quiz_id: int):  # Helper method to create the naming for the database.
    return str(quiz_id) + "_"


def get_items(quiz_id: int):  # Helper method to load all questions into a catsim-usable np array
    questions_json = r.lrange(get_r_prefix(quiz_id) + "questions", 0, r.llen(get_r_prefix(quiz_id) + "questions"))
    items = np.empty([0, 4], float)  # contains all possible questions for the quiz in the catsim format
    for question_json in questions_json:
        question_parsed = json.loads(question_json)
        items = np.append(items, [[question_parsed.get('discrimination'), question_parsed.get('difficulty'),
                                   question_parsed.get('pseudoGuessing'), question_parsed.get('upperAsymptote')]], 0)
    return items


def get_item_by_index(quiz_id: int, item_index: int):
    items = get_items(quiz_id)
    return items[item_index]


# Helper method to load all questionIds into a list, so we can use the listindex to select the chosen question id
def get_question_ids(quiz_id: int):
    questions_json = r.lrange(get_r_prefix(quiz_id) + "questions", 0, r.llen(get_r_prefix(quiz_id) + "questions"))
    question_ids = []  # contains all the real questionIds; maps to items via the index
    for question_json in questions_json:
        question_parsed = json.loads(question_json)
        question_ids.append(question_parsed.get('id'))
    return question_ids


# Helper method to load all materialIds into a list, so we can use the listindex to select the chosen material id
def get_material_ids(quiz_id: int):
    questions_json = r.lrange(get_r_prefix(quiz_id) + "questions", 0, r.llen(get_r_prefix(quiz_id) + "questions"))
    material_ids = []
    for question_json in questions_json:
        question_parsed = json.loads(question_json)
        material_ids.append(question_parsed.get('materialId'))
    return material_ids


# Helper method to retrieve a question id by its index
def get_question_id_by_index(quiz_id: int, item_index: int):
    question_ids = get_question_ids(quiz_id)
    return question_ids[item_index]


# Helper method to retrieve a material id by its index
def get_material_id_by_index(quiz_id: int, item_index: int):
    material_ids = get_material_ids(quiz_id)
    return material_ids[item_index]


def get_administered_items(quiz_id: int):
    administered_items_json = r.lrange(get_r_prefix(quiz_id) + "administeredItems", 0,
                                       r.llen(get_r_prefix(quiz_id) + "administeredItems"))
    administered_items = np.empty([0, 1], int)
    for administered_item_json in administered_items_json:
        administered_items = np.append(administered_items, int(administered_item_json))
    return administered_items


def get_responses(quiz_id: int):
    responses_json = r.lrange(get_r_prefix(quiz_id) + "responses", 0, r.llen(get_r_prefix(quiz_id) + "responses"))
    responses = np.empty([0, 1],
                         dtype=bool)  # contains the given answers for the administeredQuestions as boolean values (needed for the catsim library)
    for response_json in responses_json:
        if float(response_json) == 1.0:
            responses = np.append(responses, True)
        else:
            responses = np.append(responses, False)
    return responses


def get_responses_as_float(quiz_id: int):
    responses_json = r.lrange(get_r_prefix(quiz_id) + "responses", 0, r.llen(get_r_prefix(quiz_id) + "responses"))
    responses = np.empty([0, 1],
                         dtype=float)  # contains the given answers for the administeredQuestions as float values
    for response_json in responses_json:
        responses = np.append(responses, float(response_json))
    return responses


def get_indices(quiz_id: int):  # Helper method for non-adaptive quizzes.
    questions = get_items(quiz_id)
    indices = []
    i = 0
    while i <= len(questions):
        indices.append(i)
        i = i + 1
    return indices


def get_estimator(quiz_id: int):
    competency_estimator = r.get(get_r_prefix(quiz_id) + "competencyEstimator").decode("utf-8")
    if competency_estimator == "differentialEvolutionEstimator":
        estimator = DifferentialEvolutionEstimator(
            (float(r.get(get_r_prefix(quiz_id) + "minDiff")), float(r.get(get_r_prefix(quiz_id) + "maxDiff"))))
    return estimator


def get_selector(quiz_id: int):
    question_selector = r.get(get_r_prefix(quiz_id) + "questionSelector").decode("utf-8")
    if question_selector == 'maxInfoSelector':
        selector = MaxInfoSelector()
    elif question_selector == 'urrySelector':
        selector = UrrySelector()
    elif question_selector == 'linearSelector':
        selector = LinearSelector(get_indices(quiz_id))
    return selector


def quiz_id_exists(quiz_id: int):
    quiz_ids_json = r.lrange("quizIds", 0, r.llen("quizIds"))
    for quiz_id_json in quiz_ids_json:
        if int(quiz_id_json) == quiz_id:
            return True
    return False


# INIT Methods for CAT-SIM Objects
def init_estimator(quiz_api: QuizAPI):
    if quiz_api.competencyEstimator == 'differentialEvolutionEstimator':
        min_in_columns = np.amin(get_items(quiz_api.quizId), axis=0)
        min_diff = min_in_columns[1]
        max_in_columns = np.amax(get_items(quiz_api.quizId), axis=0)
        max_diff = max_in_columns[1]
        r.mset({get_r_prefix(quiz_api.quizId) + "minDiff": min_diff,
                get_r_prefix(quiz_api.quizId) + "maxDiff": max_diff
                })
    # could implement other estimators with other parameters


def init_selector(quiz_api: QuizAPI):
    # this implements: going through all questions in the given order and stop after the last one (because minMeasurementAccuracy=0)
    if quiz_api.questionSelector == 'linearSelector':
        quiz_api.maxNumberOfQuestions = len(quiz_api.questions)
        r.set(get_r_prefix(quiz_api.quizId) + "maxNumberOfQuestions",
              len(quiz_api.questions))  # a classic quiz will stop after all its items are delivered
        quiz_api.minMeasurementAccuracy = 0.0
        r.set(get_r_prefix(quiz_api.quizId) + "minMeasurementAccuracy",
              0.0)  # Is set to 0.0 since a non-adaptive quiz should display all questions
        quiz_api.competencyEstimator = "linearEstimator"
    # could implement other selectors with other parameters


def init_initializer(quiz_api: QuizAPI):
    if quiz_api.inputProficiencyLevel == 99.9:  # 99.9: magic value to initialize with random proficiency
        initializer = RandomInitializer()  # Initialize quiz with random proficiency level between -5 and 5
    else:
        ran = random.random()  # Initialize quiz with random proficiency level between 0 and 1
        initializer = FixedPointInitializer(ran)
    current_proficiency_level = initializer.initialize()
    r.set(get_r_prefix(quiz_api.quizId) + "estTheta", current_proficiency_level)


# queries questions for given topic and returns a list of question results
def get_questions_of_topic(topic_id: str):

    neo4j = {
        "user": 'neo4j',
        "password": urllib.parse.quote('jdUUxfTkvPyb2LZ_i-mQ5eiYOwgc1BcHfjJA0hcmQzQ'),
        "host": 'neo4j+s://feb9a4ae.databases.neo4j.io:7687',
    }

    graphdb = GraphDatabase.driver(uri=neo4j["host"], auth=(neo4j["user"], neo4j["password"]))
    session = graphdb.session()
    query = f"MATCH (n:Question) WHERE n.topic ='{topic_id}' RETURN n"
    results = session.run(query)
    nodes = json.loads(json.dumps(results.data()))  # converting results to dictionary

    questions: List[QuestionAPI] = []
    material_id = 123456  # dummy value!

    for node in nodes:
        root = node['n']
        question_id: int = root['id']
        difficulty: float = root['difficulty']
        questions.append(QuestionAPI(id=question_id, materialId=material_id, difficulty=difficulty))
        material_id += 1

    session.close()

    return questions


# returns nr of questions of given topic
def count_questions_of_topic(topic_id: str):
    session = sessionmaker(bind=engine)()
    count = session.query(Difficulty).filter(Difficulty.topic_id == topic_id).count()
    session.close()
    return count


# returns a list of tuples (topics, nr of questions in that topic) ordered by nr of questions in descending order
def get_all_topics_count():
    session = sessionmaker(bind=engine)()
    result = session.query(Difficulty.topic_id, func.count(Difficulty.topic_id)).group_by(Difficulty.topic_id).order_by(
        func.count(Difficulty.topic_id).desc()).all()
    session.close()
    return result


# class used as a structure for matching question_ids, difficulties and given answers
class Question:
    def __init__(self, question_id, initial_difficulty, answer):
        self.question_id = question_id
        self.initial_difficulty = initial_difficulty
        self.calibrated_difficulty = None
        self.answer = answer


# bundles a question_id with its difficulty and the given answer into one Question object
def match_administered_items(quiz_id: int):
    administered_items = generate_administered_items(quiz_id)  # list of QuestionAPI objects
    given_answers = get_responses(quiz_id)  # list of boolean values
    matched_items = []  # will contain Question objects
    # combine matching question_id, difficulty and response value into Question object
    for i, administered_item in enumerate(administered_items):
        matched_items.append(Question(administered_item.id, administered_item.difficulty, given_answers[i]))
    return matched_items


# calibrates all administered questions of a given quiz_id, method is called after a quiz has finished
def calibrate_questions(quiz_id: int):
    matched_items = match_administered_items(quiz_id)
    question_logs = []

    # These parameters do not change during a quiz:

    # rA: score of the student (proficiency level)
    # (proficiency level is defined when first creating the quiz)
    r_a = float(r.get(get_r_prefix(quiz_id) + "inputProficiencyLevel"))

    # pylint: disable=invalid-name
    # D: denominator
    # MST-21 workaround
    d = float(r.get("global_denominator")) if float(r.get("global_denominator")) != 0 else config.calibration[
        "denominator"]

    # K: update rate
    # MST-21 workaround
    k = float(r.get("global_update_rate")) if float(r.get("global_update_rate")) != 0 else config.calibration[
        "update_rate"]

    # assign each question a newly calibrated difficulty
    for matched_item in matched_items:
        # rB: item difficulty
        r_b = matched_item.initial_difficulty

        # EAB: probability of answering question correctly
        eab = (10 ** (r_a - r_b) / d) / (1 + (10 ** (r_a - r_b) / d))

        # pylint: disable=invalid-name
        # S: answer
        s = matched_item.answer

        # r'B: new calibrated item difficulty (cannot drop below 0)
        new_difficulty = r_b - k * (s - eab) if r_b - k * (s - eab) > 0 else 0

        # assign newly calibrated difficulty to item
        matched_item.calibrated_difficulty = new_difficulty

        # create question_log and add to list of logs
        question_logs.append(CELog(
            quiz_id=quiz_id,
            question_id=matched_item.question_id,
            answer=matched_item.answer,
            start_difficulty=matched_item.initial_difficulty,
            end_difficulty=matched_item.calibrated_difficulty,
            denominator=d,
            update_rate=k,
            student_score=r_a
        ))

    # update item difficulties in database
    update_item_difficulties(matched_items)

    # log changes
    for question_log in question_logs:
        log(question_log)
        time.sleep(0.001)  # sleep for 1ms to avoid log entries with the same log time

    return matched_items


# updates item difficulties in database with matched_items (list of Question objects)
def update_item_difficulties(calibrated_items: list):
    session = sessionmaker(bind=engine)()
    for calibrated_item in calibrated_items:
        session.execute(
            update(Difficulty)
            .where(Difficulty.question_id == calibrated_item.question_id)
            .values(difficulty=calibrated_item.calibrated_difficulty)
        )
    session.commit()
    session.close()


# returns a list of administered items in a quiz as QuestionAPI objects
def generate_administered_items(quiz_id: int):
    administered_questions: List[QuestionAPI] = []
    for item_index in get_administered_items(quiz_id):
        item = get_item_by_index(quiz_id, item_index)
        question_api = QuestionAPI(id=get_question_id_by_index(quiz_id, item_index),
                                   materialId=get_material_id_by_index(quiz_id, item_index),
                                   discrimination=item[0],
                                   difficulty=item[1], pseudoGuessing=item[2], upperAsymptote=item[3])
        administered_questions.append(question_api)
    return administered_questions


# MST-21 Careful, workaround: this sets the denominator (d) and update rate (k) globally and not per quiz!
def set_calibration_params(denominator: float, update_rate: float):
    r.mset(
        {
            "global_denominator": denominator,
            "global_update_rate": update_rate
        }
    )


# --------------- For Logging ---------------

# logs a given CELog instance to logfile
def log(entry: CELog):
    logger.info(entry.get_log_representation())
