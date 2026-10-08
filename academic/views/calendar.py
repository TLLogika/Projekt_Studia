"""Live calendar reads; personal scopes are always bound to the session person."""

from datetime import date, datetime, timedelta

from academic.auth.models import AuthorizationError
from academic.db import query
from academic.errors import ValidationError
from academic.records import record_id
from academic.importing.parser import parse_time

SCOPES = {"dean": "calendar.dean", "teacher": "calendar.teacher", "student": "calendar.student"}

FROM_SQL = """
FROM org.SemesterOrganizationSchedules sch
JOIN org.SemesterOrganizations orgn ON orgn.Id=sch.SemesterOrganizationId AND orgn.IsActive=1
JOIN org.SubjectsSemesters ss ON ss.Id=orgn.SubjectsSemesterId AND ss.IsActive=1
JOIN org.Subjects subject ON subject.Id=ss.SubjectId
JOIN org.Semesters sem ON sem.Id=ss.SemesterId
JOIN org.LectureGroups lg ON lg.Id=orgn.LectureGroupId AND lg.IsActive=1
JOIN org.SemesterCalendarDates cd ON cd.Id=sch.SemesterCalendarDateId AND cd.IsActive=1
JOIN org.SemesterCalendars cal ON cal.Id=cd.SemesterCalendarId AND cal.IsActive=1
JOIN org.DailySchedules ds ON ds.Id=sch.DailyScheduleId AND ds.IsActive=1
LEFT JOIN org.Teachers teacher ON teacher.Id=orgn.TeacherId
LEFT JOIN org.Persons teacher_person ON teacher_person.Id=teacher.PersonId
LEFT JOIN org.Teachers additional_teacher ON additional_teacher.Id=sch.TeacherId
LEFT JOIN org.Persons additional_person ON additional_person.Id=additional_teacher.PersonId
CROSS APPLY (SELECT DATEADD(minute,
    DATEDIFF(minute,CONVERT(time,'00:00'),TRY_CONVERT(time,ds.StartHour)),
    CONVERT(datetime2,CONVERT(date,cd.[Date]))) AS EventStart) clock
"""

# EndDate is exclusive. Membership is checked at the actual slot start,
# never against legacy Students.GroupId or today's membership.
STUDENT_SQL = """
EXISTS (
 SELECT 1 FROM org.Students student
 JOIN org.GroupMembers member ON member.StudentId=student.Id AND member.IsActive=1
 JOIN org.Groups student_group ON student_group.Id=member.GroupId AND student_group.IsActive=1
 JOIN org.LectureGroupsElements element ON element.GroupId=member.GroupId AND element.IsActive=1
 WHERE student.PersonId=? AND element.LectureGroupId=orgn.LectureGroupId
   AND student_group.SemesterId=ss.SemesterId
   AND clock.EventStart IS NOT NULL
   AND (member.StartDate IS NULL OR member.StartDate<=clock.EventStart)
   AND (member.EndDate IS NULL OR member.EndDate>clock.EventStart)
)
"""


def calendar_sql(scope: str):
    """A fixed scope predicate. User-selected IDs can only narrow it."""
    if scope == "dean":
        return "1=1", 0
    if scope == "teacher":
        return "(teacher.PersonId=? OR additional_teacher.PersonId=?)", 2
    if scope == "student":
        return STUDENT_SQL, 1
    raise AuthorizationError("Nieobsługiwany zakres kalendarza.")


class CalendarService:
    def __init__(self, db, auth):
        self.db, self.auth = db, auth

    def scopes(self, token) -> tuple[str, ...]:
        return tuple(scope for scope, code in SCOPES.items() if self.auth.can(token, code, "view"))

    def _access(self, token, scope, connection):
        if scope not in SCOPES:
            raise AuthorizationError("Nieobsługiwany zakres kalendarza.")
        self.auth.require(token, SCOPES[scope], "view", connection=connection)
        user = self.auth.get_user(token)
        predicate, count = calendar_sql(scope)
        return predicate, (user.person_id,) * count

    def semesters(self, token, scope):
        with self.db.connection() as connection:
            predicate, params = self._access(token, scope, connection)
            return query(connection, "SELECT DISTINCT sem.Id,sem.Name,sem.StartDate " + FROM_SQL +
                         " WHERE sch.IsActive=1 AND " + predicate + " ORDER BY sem.StartDate DESC,sem.Name", params)

    def lecture_groups(self, token, scope, semester_id: str | None = None):
        """Offer only actual groups represented in the authorized calendar."""
        with self.db.connection() as connection:
            predicate, params = self._access(token, scope, connection)
            where = " WHERE sch.IsActive=1 AND " + predicate
            if semester_id is not None:
                where += " AND ss.SemesterId=?"
                params += (record_id(semester_id),)
            return query(connection, "SELECT DISTINCT lg.Id,lg.Name,lg.SemesterId " + FROM_SQL +
                         where + " ORDER BY lg.Name,lg.Id", params)

    def list(self, token, scope: str, start: date, end: date, semester_id: str | None = None,
             lecture_group_id: str | None = None):
        if type(start) is not date or type(end) is not date or end < start:
            raise ValidationError("Wybierz poprawny zakres dat kalendarza.")
        if (end - start).days > 366:
            raise ValidationError("Zakres pojedynczego odczytu kalendarza może obejmować najwyżej 367 dni.")
        with self.db.connection() as connection:
            predicate, scope_params = self._access(token, scope, connection)
            select = """
SELECT sch.Id,sch.Timestamp,sch.SemesterOrganizationId,sch.TeacherId,
       orgn.TeacherId AS OrganizationTeacherId,ss.SemesterId,sem.Name AS SemesterName,
       subject.Name AS SubjectName,lg.Name AS LectureGroupName,cd.[Date],ds.StartHour,ds.EndHour,
       sch.LectureRoomName,sch.IsComplited,sch.IsCanceled,sch.IsMoved,
       CONCAT(teacher_person.FirstName1,' ',teacher_person.LastName) AS TeacherName,
       CONCAT(additional_person.FirstName1,' ',additional_person.LastName) AS AdditionalTeacherName
"""
            where = " WHERE sch.IsActive=1 AND " + predicate + " AND cd.[Date]>=? AND cd.[Date]<?"
            params = scope_params + (datetime.combine(start, datetime.min.time()), datetime.combine(end + timedelta(days=1), datetime.min.time()))
            if semester_id is not None:
                where += " AND ss.SemesterId=?"
                params += (record_id(semester_id),)
            if lecture_group_id is not None:
                where += " AND orgn.LectureGroupId=?"
                params += (record_id(lecture_group_id),)
            rows = query(connection, select + FROM_SQL + where + " ORDER BY cd.[Date],ds.StartHour,lg.Name,subject.Name,sch.Id", params)
            for row in rows:
                try:
                    first, last = parse_time(row["StartHour"]), parse_time(row["EndHour"])
                    if last <= first:
                        raise ValidationError("Nieprawidłowy zakres slotu.")
                except ValidationError:
                    raise ValidationError("Harmonogram zawiera slot o niepoprawnych godzinach. Popraw istniejący plan dnia.") from None
            return rows
