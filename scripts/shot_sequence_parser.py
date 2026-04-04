import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Union


class Depth(Enum):
    UNKNOWN_DEPTH = "UNKNOWN_DEPTH"
    SHALLOW = "SHALLOW"
    DEEP = "DEEP"
    BASELINE = "BASELINE"


class Direction(Enum):
    UNKNOWN_DIRECTION = "UNKNOWN_DIRECTION"
    RIGHT = "RIGHT"
    CENTER = "CENTER"
    LEFT = "LEFT"


class ShotType(Enum):
    UNKNOWN_SHOT_TYPE = "UNKNOWN_SHOT_TYPE"
    FOREHAND = "FOREHAND"
    BACKHAND = "BACKHAND"
    FOREHAND_SLICE = "FOREHAND_SLICE"
    BACKHAND_SLICE = "BACKHAND_SLICE"
    FOREHAND_VOLLEY = "FOREHAND_VOLLEY"
    BACKHAND_VOLLEY = "BACKHAND_VOLLEY"
    SERVE = "SERVE"
    SMASH = "SMASH"
    BACKHAND_SMASH = "BACKHAND_SMASH"
    FOREHAND_DROP = "FOREHAND_DROP"
    BACKHAND_DROP = "BACKHAND_DROP"
    FOREHAND_LOB = "FOREHAND_LOB"
    BACKHAND_LOB = "BACKHAND_LOB"
    FOREHAND_HALF_VOLLEY = "FOREHAND_HALF_VOLLEY"
    BACKHAND_HALF_VOLLEY = "BACKHAND_HALF_VOLLEY"
    FOREHAND_SWINGING_VOLLEY = "FOREHAND_SWINGING_VOLLEY"
    BACKHAND_SWINGING_VOLLEY = "BACKHAND_SWINGING_VOLLEY"
    TRICK = "TRICK"


class ServeDirection(Enum):
    UNKNOWN_SERVE_DIRECTION = "UNKNOWN_SERVE_DIRECTION"
    T = "T"
    BODY = "BODY"
    WIDE = "WIDE"


class CourtPosition(Enum):
    UNKNOWN_COURT_POSITION = "UNKNOWN_COURT_POSITION"
    APPROACH = "APPROACH"
    NET = "NET"
    BASELINE = "BASELINE"


class Outcome(Enum):
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    CONTINUE = "CONTINUE"
    WINNER = "WINNER"
    UNFORCED_ERROR = "UNFORCED_ERROR"
    FORCED_ERROR = "FORCED_ERROR"


class ErrorType(Enum):
    NO_ERROR = "NO_ERROR"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    NET = "NET"
    WIDE = "WIDE"
    DEEP = "DEEP"
    WIDE_AND_DEEP = "WIDE_AND_DEEP"


@dataclass
class ShotDetail:
    number: int
    shot_type: ShotType
    depth: Depth
    direction: Direction
    court_position: CourtPosition
    outcome: Outcome
    serve_direction: ServeDirection
    error_type: ErrorType
    point_number: Optional[int]
    point_match_id: Optional[Union[str, object]]

    def as_dict(self) -> dict:
        # Include notebook-level fields and compatibility aliases used by callers.
        return {
            "number": self.number,
            "shot_num": self.number,
            "shot_type": self.shot_type.value,
            "depth": self.depth.value,
            "direction": self.direction.value,
            "court_position": self.court_position.value,
            "outcome": self.outcome.value,
            "serve_direction": self.serve_direction.value,
            "error_type": self.error_type.value,
            "point_number": self.point_number,
            "point_match_id": self.point_match_id,
        }


class ShotSequenceParser:
    """Parser ported from db/process_tennis.ipynb shot parsing logic."""

    DEPTH_CHAR_TO_DEPTH: Dict[str, Depth] = {
        "7": Depth.SHALLOW,
        "8": Depth.DEEP,
        "9": Depth.BASELINE,
    }

    DIRECTION_CHAR_TO_DIRECTION: Dict[str, Direction] = {
        "1": Direction.RIGHT,
        "2": Direction.CENTER,
        "3": Direction.LEFT,
    }

    SHOT_CODE_TO_TYPE: Dict[str, ShotType] = {
        "f": ShotType.FOREHAND,
        "b": ShotType.BACKHAND,
        "r": ShotType.FOREHAND_SLICE,
        "s": ShotType.BACKHAND_SLICE,
        "v": ShotType.FOREHAND_VOLLEY,
        "z": ShotType.BACKHAND_VOLLEY,
        "o": ShotType.SMASH,
        "p": ShotType.BACKHAND_SMASH,
        "u": ShotType.FOREHAND_DROP,
        "y": ShotType.BACKHAND_DROP,
        "l": ShotType.FOREHAND_LOB,
        "m": ShotType.BACKHAND_LOB,
        "h": ShotType.FOREHAND_HALF_VOLLEY,
        "i": ShotType.BACKHAND_HALF_VOLLEY,
        "j": ShotType.FOREHAND_SWINGING_VOLLEY,
        "k": ShotType.BACKHAND_SWINGING_VOLLEY,
        "t": ShotType.TRICK,
        "q": ShotType.UNKNOWN_SHOT_TYPE,
    }

    SERVE_DIRECTION_MAP: Dict[str, ServeDirection] = {
        "4": ServeDirection.WIDE,
        "5": ServeDirection.BODY,
        "6": ServeDirection.T,
    }

    COURT_POSITION_CHAR_TO_COURT_POSITION: Dict[str, CourtPosition] = {
        "+": CourtPosition.APPROACH,
        "-": CourtPosition.NET,
        "=": CourtPosition.BASELINE,
    }

    OUTCOME_CHAR_TO_OUTCOME: Dict[str, Outcome] = {
        "*": Outcome.WINNER,
        "@": Outcome.UNFORCED_ERROR,
        "#": Outcome.FORCED_ERROR,
    }

    ERROR_TYPE_CHAR_TO_ERROR_TYPE: Dict[str, ErrorType] = {
        "n": ErrorType.NET,
        "w": ErrorType.WIDE,
        "d": ErrorType.DEEP,
        "x": ErrorType.WIDE_AND_DEEP,
    }

    def __init__(self) -> None:
        all_shot_type_char_codes = "".join(self.SHOT_CODE_TO_TYPE.keys())
        self.split_regex = r"([" + all_shot_type_char_codes + r"]){1}"

    def _parse_serve_str(
        self,
        serve_shot_str: str,
        shot_number: int,
        point_number: Optional[int] = None,
        point_match_id: Optional[Union[str, object]] = None,
    ) -> ShotDetail:
        serve_direction = ServeDirection.UNKNOWN_SERVE_DIRECTION
        outcome = Outcome.CONTINUE
        error_type = ErrorType.NO_ERROR

        for shot_property_char in serve_shot_str:
            if shot_property_char in self.SERVE_DIRECTION_MAP:
                serve_direction = self.SERVE_DIRECTION_MAP[shot_property_char]
            if shot_property_char in self.OUTCOME_CHAR_TO_OUTCOME:
                outcome = self.OUTCOME_CHAR_TO_OUTCOME[shot_property_char]
            if shot_property_char in self.ERROR_TYPE_CHAR_TO_ERROR_TYPE:
                error_type = self.ERROR_TYPE_CHAR_TO_ERROR_TYPE[shot_property_char]

        return ShotDetail(
            number=shot_number,
            shot_type=ShotType.SERVE,
            depth=Depth.UNKNOWN_DEPTH,
            direction=Direction.UNKNOWN_DIRECTION,
            court_position=CourtPosition.UNKNOWN_COURT_POSITION,
            outcome=outcome,
            serve_direction=serve_direction,
            error_type=error_type,
            point_number=point_number,
            point_match_id=point_match_id,
        )

    def parse_shot_string_into_arr(
        self,
        first_shot_str: str,
        second_shot_str: str = "",
        point_number: Optional[int] = None,
        point_match_id: Optional[Union[str, object]] = None,
    ) -> List[ShotDetail]:
        shot_details: List[ShotDetail] = []
        first_shot_str = first_shot_str or ""
        second_shot_str = second_shot_str or ""

        shot_number = 0
        shot_str = first_shot_str
        if second_shot_str != "":
            shot_details.append(
                self._parse_serve_str(first_shot_str, shot_number, point_number, point_match_id)
            )
            shot_number += 1
            shot_str = second_shot_str

        shot_strs_split_by_type = [
            shot_str_piece
            for shot_str_piece in re.split(self.split_regex, shot_str)
            if len(shot_str_piece) > 0
        ]
        if not shot_strs_split_by_type:
            return shot_details

        serve_shot_str = shot_strs_split_by_type[0]
        shot_details.append(
            self._parse_serve_str(serve_shot_str, shot_number, point_number, point_match_id)
        )
        shot_number += 1

        # Follows notebook logic: pair each shot-type token with the next token.
        for first_parsed_char_shot, second_parsed_char_shot in zip(
            shot_strs_split_by_type[1:],
            shot_strs_split_by_type[2:],
        ):
            if (
                first_parsed_char_shot in self.SHOT_CODE_TO_TYPE
                and second_parsed_char_shot in self.SHOT_CODE_TO_TYPE
            ):
                shot_type = self.SHOT_CODE_TO_TYPE[first_parsed_char_shot]
                shot_details.append(
                    ShotDetail(
                        number=shot_number,
                        shot_type=shot_type,
                        depth=Depth.UNKNOWN_DEPTH,
                        direction=Direction.UNKNOWN_DIRECTION,
                        court_position=CourtPosition.UNKNOWN_COURT_POSITION,
                        outcome=Outcome.CONTINUE,
                        serve_direction=ServeDirection.UNKNOWN_SERVE_DIRECTION,
                        error_type=ErrorType.NO_ERROR,
                        point_number=point_number,
                        point_match_id=point_match_id,
                    )
                )
            elif (
                first_parsed_char_shot in self.SHOT_CODE_TO_TYPE
                and second_parsed_char_shot not in self.SHOT_CODE_TO_TYPE
            ):
                shot_type = self.SHOT_CODE_TO_TYPE[first_parsed_char_shot]
                depth = Depth.UNKNOWN_DEPTH
                direction = Direction.UNKNOWN_DIRECTION
                court_position = CourtPosition.UNKNOWN_COURT_POSITION
                outcome = Outcome.CONTINUE

                for shot_property_char in second_parsed_char_shot:
                    if shot_property_char in self.DEPTH_CHAR_TO_DEPTH:
                        depth = self.DEPTH_CHAR_TO_DEPTH[shot_property_char]
                    if shot_property_char in self.DIRECTION_CHAR_TO_DIRECTION:
                        direction = self.DIRECTION_CHAR_TO_DIRECTION[shot_property_char]
                    if shot_property_char in self.OUTCOME_CHAR_TO_OUTCOME:
                        outcome = self.OUTCOME_CHAR_TO_OUTCOME[shot_property_char]
                    if shot_property_char in self.COURT_POSITION_CHAR_TO_COURT_POSITION:
                        court_position = self.COURT_POSITION_CHAR_TO_COURT_POSITION[shot_property_char]

                shot_details.append(
                    ShotDetail(
                        number=shot_number,
                        shot_type=shot_type,
                        depth=depth,
                        direction=direction,
                        court_position=court_position,
                        outcome=outcome,
                        serve_direction=ServeDirection.UNKNOWN_SERVE_DIRECTION,
                        error_type=ErrorType.NO_ERROR,
                        point_number=point_number,
                        point_match_id=point_match_id,
                    )
                )
                shot_number += 1

        return shot_details

    def parse(self, sequence: str) -> List[dict]:
        """
        Compatibility entrypoint used by data_pipeline.
        Treats `sequence` as a single serve+rally string and returns dict records.
        """
        return [shot_detail.as_dict() for shot_detail in self.parse_shot_string_into_arr(sequence, "")]
