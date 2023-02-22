from typing import Optional, List

from pydantic import BaseModel

import config


# Question object for API --> Used for quiz creation
class QuestionAPI(BaseModel):
    id: int
    materialId: str
    discrimination: Optional[float] = 1.0
    difficulty: float
    pseudoGuessing: Optional[float] = 0.0
    upperAsymptote: Optional[float] = 1.0


# QuizAPI object for API --> Used for quiz creation
class QuizAPI(BaseModel):
    quizId: Optional[int]
    maxNumberOfQuestions: Optional[int] = config.defaultAdaptiveQuiz["maxNumberOfQuestions"]
    minMeasurementAccuracy: Optional[float] = config.defaultAdaptiveQuiz["minMeasurementAccuracy"]
    inputProficiencyLevel: Optional[float] = config.defaultAdaptiveQuiz["inputProficiencyLevel"]
    questionSelector: Optional[str] = config.defaultAdaptiveQuiz["questionSelector"]
    competencyEstimator: Optional[str] = config.defaultAdaptiveQuiz["competencyEstimator"]
    topicId: Optional[str] = config.defaultAdaptiveQuiz["topicId"]
    questions: Optional[List[QuestionAPI]] = []


# Answer object for API --> Used for sending the answer(isCorrect) of the current question of the quiz in the request
class AnswerAPI(BaseModel):
    isCorrect: Optional[float] = None


# Question object for API --> Used to send the next question as a response
class NextQuestionAPI(BaseModel):
    quizId: int
    questionId: Optional[int] = None
    materialId: Optional[str] = None
    measurementAccuracy: float
    currentCompetency: float
    quizFinished: bool


# Result object for API --> Used to send the quiz result in the response.
class ResultAPI(BaseModel):
    quizId: int
    quizFinished: bool
    currentCompetency: float
    measurementAccuracy: float
    maxNumberOfQuestions: int
    administeredQuestions: List[
        QuestionAPI] = []  # contains all questions administered during the quiz including their difficulty
    responses: List[float] = []  # contains the given answers for the administeredQuestions


# QuizID object for API
class QuizIdAPI(BaseModel):
    quizId: int
