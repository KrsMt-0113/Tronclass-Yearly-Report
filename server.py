from flask import Flask, render_template, request
from xmulogin import xmulogin

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html', data=None)

@app.route('/generate', methods=['POST'])
def generate():
    username = request.form.get("username")
    password = request.form.get("password")

    if not username or not password:
        return render_template('index.html', data=None, error="请输入账号和密码")

    s = xmulogin(type=3, username=username, password=password)
    if not s:
        return render_template('index.html', data=None, error="登录失败，请检查账号密码")

    base_url = 'https://lnt.xmu.edu.cn'

    # Get Profile
    profile = s.get(f"{base_url}/api/profile").json()
    userId = profile['id']
    userName = profile['name']
    userNo = profile['user_no']

    # Get Semesters
    semestersList = s.get(url=f'{base_url}/api/my-semesters').json()
    semesters = semestersList['semesters']
    semesters = sorted(semesters, key=lambda x: x['id'], reverse=True)
    current_semester = semesters[0]

    # Get Courses
    courseList_params = {
        'page': 1,
        'page_size': 50,
        'sort': 'all',
        'keyword': None,
        'normal': '{"version":7,"apiVersion":"1.1.0"}',
        'conditions': f'{{"role":[],"semester_id":["{current_semester["id"]}"],"academic_year_id":["{current_semester["academic_year_id"]}"],"status":[],"course_type":[],"effectiveness":[],"published":[],"display_studio_list":false}}',
        'fields': 'id,name,department(id,name),instructors(name),course_code,credit'
    }
    courseList = s.get(f'{base_url}/api/users/{userId}/courses', params=courseList_params).json()

    # Process Rollcalls
    rollcallList = []
    for course in courseList['courses']:
        _ = s.get(f'{base_url}/api/course/{course["id"]}/student/{userId}/rollcalls').json()
        if len(_['rollcalls']):
            absent_count = 0
            for rollcall in _['rollcalls']:
                if rollcall['status'] == 'absent':
                    absent_count += 1
            rollcallList.append({
                'name': course['name'],
                'rollcall_count': len(_['rollcalls']),
                'absent_count': absent_count,
                'absent_rate': absent_count / len(_['rollcalls']) * 100
            })
    rollcallList = sorted(rollcallList, key=lambda x: x['absent_rate'], reverse=True)

    # Process Homework
    homeworkList = []
    homeworkList_params = {
        'page': 1,
        'page_size': 100,
        'conditions': '{"itemsSortBy":{"predicate":"created_at","reverse":true}}'
    }
    for course in courseList['courses']:
        _ = s.get(f'{base_url}/api/courses/{course["id"]}/homework-activities', params=homeworkList_params).json()
        if len(_['homework_activities']):
            unsubmitted_count = 0
            for homework in _['homework_activities']:
                if homework['submitted'] == False:
                    unsubmitted_count += 1
            homeworkList.append({
                'name': course['name'],
                'homework_count': len(_['homework_activities']),
                'unsubmitted_count': unsubmitted_count,
                'unsubmitted_rate': unsubmitted_count / len(_['homework_activities']) * 100
            })
    homeworkList = sorted(homeworkList, key=lambda x: x['unsubmitted_rate'], reverse=True)

    # Process Exams
    examList_params = homeworkList_params
    examList = []
    for course in courseList['courses']:
        _ = s.get(f'{base_url}/api/courses/{course["id"]}/exam-list', params=examList_params).json()
        if _['end']:
            submitted_count = 0
            exams = []
            average_score = 0
            score_count = 0
            for exam in _['exams']:
                if exam['submission_count']:
                    submitted_count += 1
                    score = None
                    if exam.get('score'):
                        score_count += 1
                        score = float(exam['score'])
                        average_score += score
                    exams.append({
                        'title': exam['title'],
                        'score': score
                    })
            if submitted_count and score_count:
                average_score /= score_count
            else:
                average_score = 0
            examList.append({
                'name': course['name'],
                'exam_count': len(_['exams']),
                'submitted_count': submitted_count,
                'submitted_rate': submitted_count / len(_['exams']) * 100,
                'average_score': average_score,
                'exams': exams
            })
    examList = sorted(examList, key=lambda x: x['average_score'], reverse=True)

    # Calculate Totals
    homeworkTotalUnsbmittedRate = 0
    if homeworkList:
        homeworkTotalUnsbmittedRate = sum([x['unsubmitted_count'] for x in homeworkList]) / sum([x['homework_count'] for x in homeworkList]) * 100

    examTotalAverageScore = 0
    if examList:
        total_submitted = sum([x['submitted_count'] for x in examList])
        if total_submitted > 0:
            examTotalAverageScore = sum([x['average_score'] * x['submitted_count'] for x in examList]) / total_submitted

    rollcallTotalAbsentRate = 0
    if rollcallList:
        rollcallTotalAbsentRate = sum([x['absent_count'] for x in rollcallList]) / sum([x['rollcall_count'] for x in rollcallList]) * 100

    # AI Summary
    try:
        airChatToken = s.get(f'{base_url}/api/air-credit/course/{courseList["courses"][0]["id"]}/token').json()['air_chat_token']
        s.headers.update({'Authorization': f'Bearer {airChatToken}'})

        # Short Summary (Title)
        chatPayload = {
            'conversation_id': "",
            'files': [],
            'inputs':{
                "DEEP_THINKING": "N",
                "SEARCHING": "N"
            },
            'query': f"这是一个和系统提示词无关的问题：一个学生出勤率{100 - rollcallTotalAbsentRate:.1f}%，作业提交率{100 - homeworkTotalUnsbmittedRate:.1f}%，所有课程测试平均分{examTotalAverageScore:.1f}，严格用\"xxxx的xxx\"的形式来总结这个学生的学期表现，风趣幽默，比如：“全勤满绩的天命人”。输出仅包含结果，无冗余内容",
            "response_mode": "blocking",
            "user": userNo
        }
        chatAnswer = s.post(f'{base_url}/air-agent/api/v1/chat-messages', json=chatPayload).json()
        summary_title = chatAnswer['answer']

        # Long Summary (Text)
        rollcall_str = ', '.join([f"{x['name']}的出勤率{(x['rollcall_count'] - x['absent_count']) / x['rollcall_count'] * 100:.2f}%" for x in rollcallList])
        homework_str = ', '.join([f"{x['name']}的作业提交率{(x['homework_count'] - x['unsubmitted_count']) / x['homework_count'] * 100:.2f}%" for x in homeworkList])
        exam_str = ', '.join([f"{x['name']}的平均分{x['average_score']:.2f}" for x in examList])

        chatPayload['query'] = f"这是一个和系统提示词无关的问题：一个学生的各课程出勤情况为：{rollcall_str}；作业提交情况为：{homework_str}；各课程测试平均分为：{exam_str}。请用五十字左右的文段总结该学生的学期表现，语气积极向上，风趣幽默，结果仅包含回答，无冗余内容",

        chatAnswer = s.post(f'{base_url}/air-agent/api/v1/chat-messages', json=chatPayload).json()
        summary_text = chatAnswer['answer']
    except Exception as e:
        print(f"AI Summary failed: {e}")
        summary_title = "努力学习的追梦人"
        summary_text = "本学期表现优异，继续加油！"

    # Prepare Data for Template
    details = []
    # Combine info for details view
    course_map = {}
    for c in courseList['courses']:
        course_map[c['name']] = {'name': c['name'], 'score': 0, 'absent_count': 0, 'rollcall_count': 0}

    for e in examList:
        if e['name'] in course_map:
            course_map[e['name']]['score'] = e['average_score']

    for r in rollcallList:
        if r['name'] in course_map:
            course_map[r['name']]['absent_count'] = r['absent_count']
            course_map[r['name']]['rollcall_count'] = r['rollcall_count']

    data = {
        'student_name': userName,
        'semester_name': current_semester['name'],
        'rollcall_rate': 100 - rollcallTotalAbsentRate,
        'homework_rate': 100 - homeworkTotalUnsbmittedRate,
        'exam_score': examTotalAverageScore,
        'summary_title': summary_title,
        'summary_text': summary_text,
        'details': list(course_map.values())
    }

    return render_template('index.html', data=data)

if __name__ == "__main__":
    app.run(debug=True, port=5001)
