import urllib
from distutils.util import strtobool

import requests
from fastapi import HTTPException, Form
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from starlette.responses import RedirectResponse

import config
import src.cat.cat_engine as ce
import src.cat.cat_engine_logging
from src.cat.db_connector import r
from src.models.fastapi_models import QuizAPI, AnswerAPI, QuizIdAPI

CATModule = FastAPI()  # Used for REST API
templates = Jinja2Templates(directory="templates")


# --------------- REST CALLS ---------------

@CATModule.get('/', response_class=HTMLResponse)
async def get_form(request: Request):
    topics = ce.get_all_topics_count()  # topics = list of tuples (topic, nr of questions)
    return templates.TemplateResponse("home.html", {"request": request, "topics": topics})


@CATModule.post('/', response_class=HTMLResponse)
async def post_form(topic: str = Form(...), denominator: float = Form(...), update_rate: float = Form(...), mode: str = Form(...)):
    url = f'{config.API_URL}/quizzes'
    body = {"topic": topic, "mode": mode, "language": "en-US"}
    response = requests.post(url, body)
    response.raise_for_status()
    response_json = response.json()
    ggb_page = f'{config.FRONTEND_URL}/q/' + f"{response_json['id']}?quizToken={urllib.parse.quote(response_json['token'])}"
    # MST-21 Careful, workaround: this sets the denominator (d) and update rate (k) globally and not per quiz!
    # TODO: d and k must be sent to GGB and then be sent back to us + maybe add a few more placeholders just in case
    ce.set_calibration_params(denominator, update_rate)
    question_redirect = RedirectResponse(ggb_page)
    return question_redirect

@CATModule.get('/update-log-database',
               summary="Updates the log database with the log file",
               tags=["log"])
async def update_log_database():
    return src.cat.cat_engine_logging.log_to_database()


@CATModule.post("/quiz",
                status_code=201,
                summary="Create a new quiz",
                tags=["quiz"])
async def api_create_quiz(quiz_api: QuizAPI):
    """
    Create a new quiz, this can either be adaptive or classic:

    Adaptive QuizAPI:
    - **quizId**: Not necessary, will be replaced by an automatically created unique ID.
    - **maxNumberOfQuestions**: The maximum amount of questions for the quiz. This will be used as a stopping criteria for the exam.
    - **minMeasurementAccuracy**: The threshold for the Standard Error of Estimation. This will be used as a stopping criteria for the exam.
    - **questionSelector**: Defines how the next question is selected. 'maxInfoSelector' represents the Maximum Information Selector (https://douglasrizzo.com.br/catsim/selection.html#catsim.selection.MaxInfoSelector) for adaptive quizzes. This is also the default.
    - **competencyEstimator**: Defines how the competency is calculated. 'differentialEvolutionEstimator' is the default Estimator, no others are currently implemented.
    - **topicId**: The ID of the topic from which questions should be taken.
    - **questions**: Not necessary, list of questions will be automatically created by topicID.

    Example request body to create an adaptive quiz:<br>
    {<br>
        "topicId": "equations",<br>
        "maxNumberOfQuestions": 20,<br>
        "minMeasurementAccuracy": 0.8,<br>
        "questionSelector": "maxInfoSelector"<br>
    }

    Classic QuizAPI:
    - **quizId**: Not necessary, will be replaced by an automatically created unique ID.
    - **maxNumberOfQuestions**: Not necessary since the number of quiz questions is determined by the length of the questions list.
    - **minMeasurementAccuracy**: Not necessary since a classic quiz is finished when all questions are delivered.
    - **questionSelector**: Defines how the next question is selected. 'linearSelector' represents the Selector for a non-adaptive Test (https://douglasrizzo.com.br/catsim/selection.html#catsim.selection.LinearSelector).
    - **competencyEstimator**: Defines how the competency is calculated. Non-adaptive tests calculate the overall percentage of correct answers as competency.
    - **topicId**: The ID of the topic from which questions should be taken.
    - **questions**: Not necessary, list of questions will be automatically created by topicID.

    Example request body to create a classic quiz:<br>
    {<br>
        "topicId": "equations",<br>
        "questionSelector": "linearSelector"<br>
    }

    Response:
    - **quizId**: Unique ID of the quiz.
    - **Other**: The other contents of the response represent the configuration of the quiz
    """
    # TODO (old) validate maxNumberOfQuestions -> It must be less or equal
    # than the number of given questions
    return ce.create_quiz(quiz_api)


@CATModule.post("/quiz/{quiz_id}/question",
                summary="Get the next question of the quiz by quizID and also send the answer to the previous question",
                tags=["question"])
async def api_get_next_question(quiz_id: int, answer: AnswerAPI):
    """
    Get the next question of the quiz by quizID and also send the answer to the previous question:

    - **quizId**: Unique ID of the quiz (was returned by the POST quiz request)
    - **isCorrect**: Correctness of the previous question. In the case of an adaptive quiz: 1.0 if the previous answer was correct, <1.0 if the previous answer was incorrect. In the case of a non-adaptive quiz, this represents the percentage of correctness of the answer. When requesting the first quiz question this will be ignored.

    Response:
    - **quizId**: Unique ID of the quiz.
    - **questionId**: The ID for the next question to be administered. Not set if quizFinished=true.
    - **materialId**: The materialId for the next question to be administered.
    - **measurementAccuracy**: Standard Estimation Error of the current competency.
    - **currentCompetency**: Describes the proficiency of the examinee.
    - **quizFinished**: True if the quiz is already finished.
    """
    if not ce.quiz_id_exists(quiz_id):
        raise HTTPException(
            status_code=404, detail="QuizAPI with id " + str(quiz_id) + " not found!")
    if strtobool(r.get(ce.get_r_prefix(quiz_id) + "quizFinished").decode()):
        raise HTTPException(
            status_code=406, detail="No more questions for quiz with id " + str(quiz_id) + "!")
    return ce.get_next_question(quiz_id, answer.isCorrect)


@CATModule.get("/quiz/{quiz_id}/result",
               summary="Get the result of quiz with ID",
               tags=["result"])
async def api_get_result(quiz_id: int):
    """
    Get the result of the quiz by quizID:

    - **quizId**: Unique ID of the quiz (was returned by the POST quiz request).

    Response:
    - **quizId**: Unique ID of the quiz.
    - **quizFinished**: Shows if the quiz is already finished.
    - **currentCompetency**: Describes the proficiency of the examinee. For non-adaptive tests, this is the percentage of correct answers.
    - **measurementAccuracy**: Standard Estimation Error of the current competency (for adaptive tests). Has no meaning for non-adaptive tests.
    - **administeredQuestions**: An ordered list of of all the questions administered.
    - **responses**: An ordered list of the responses to the administered questions.
    - **maxNumberOfQuestions**: The maximum number of questions the quiz could have had.
    """
    if not ce.quiz_id_exists(quiz_id):
        raise HTTPException(status_code=404, detail="QuizAPI with id " +
                                                    str(quiz_id) + " not found!")
    if r.get(
            ce.get_r_prefix(
                quiz_id) +
            "questionSelector").decode("utf-8") == 'linearSelector' and (
            not (
                    strtobool(
                        r.get(
                            ce.get_r_prefix(
                                quiz_id) +
                            "quizFinished").decode()))):
        raise HTTPException(status_code=406, detail="QuizAPI with id " +
                                                    str(quiz_id) + " has not been finished yet!")
    return ce.get_result(quiz_id)


@CATModule.delete("/quiz", summary="Delete quiz with ID", tags=["quiz"])
async def api_delete_quiz(quiz_id_api: QuizIdAPI):
    """
    Delete the quiz with the defined quizId:

    - **quizId**: Unique ID of the quiz (was returned by the POST quiz request)

    Response:
    Status 200 OK if the quiz was successfully deleted.
    """
    if not ce.quiz_id_exists(quiz_id_api.quizId):
        raise HTTPException(status_code=404, detail="QuizAPI with id " +
                                                    str(quiz_id_api.quizId) + " not found!")
    ce.delete_quiz(quiz_id_api)
    return ("QuizAPI with id " +
            str(quiz_id_api.quizId) +
            " was successfully deleted!")


@CATModule.get("/", status_code=200)
async def get_status200():
    return ()
