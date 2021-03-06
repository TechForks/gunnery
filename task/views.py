import json
from django import template
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.core.urlresolvers import reverse
from django.core.serializers.json import DjangoJSONEncoder
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from .forms import *
from core.views import get_common_page_data
from core.models import *
from .models import *
from django.db import transaction

@login_required
def task_page(request, task_id = None):
	data = get_common_page_data(request)
	data['task'] = get_object_or_404(Task, pk=task_id)
	return render(request, 'page/task.html', data)

@login_required
@transaction.atomic
def task_execute_page(request, task_id, environment_id=None):
	data = get_common_page_data(request)
	task = get_object_or_404(Task, pk=task_id)
	data['task'] = task
	if environment_id:
		environment = get_object_or_404(Environment, pk=int(environment_id))
		data['environment'] = environment
		if task.application.id != environment.application.id:
			raise ValueError('task.application.id did not match with environment.application.id')

	if request.method == 'POST':
		parameter_prefix = 'parameter-'
		parameters = {}
		for name, value in request.POST.items():
			if name.startswith(parameter_prefix):
				name = name[len(parameter_prefix):]
				parameters[name] = value

		# @todo validate parameter names

		environment = get_object_or_404(Environment, pk=int(parameters['environment']))
		if task.application.id != environment.application.id:
			raise ValueError('task.application.id did not match with environment.application.id')

		duplicateExecution = Execution.objects.filter(task=task, environment=environment,
			status__in=[Execution.PENDING, Execution.RUNNING])
		if duplicateExecution.count():
			data['duplicate_error'] = True
			data['task'] = task
			data['environment'] = environment
		else:
			execution = Execution(task=task, environment=environment, user=request.user)
			execution.save()

			for name, value in parameters.items():
				if name != 'environment':
					ExecutionParameter(execution=execution, name=name, value=value).save()

			for command in execution.commands.all():
				command.replace_params()
				command.save()
			execution.start()
			return redirect(execution)

	return render(request, 'page/task_execute.html', data)

@login_required
def task_form_page(request, application_id = None, task_id = None):
	data = get_common_page_data(request)
	if task_id:
		task = get_object_or_404(Task, pk=task_id)
		application = task.application
		data['task'] = task
		args = {}
	elif application_id:
		application = get_object_or_404(Application, pk=application_id)
		args = {'application_id':application_id}
	form, form_parameters, form_commands = create_forms(request, task_id, args)

	if request.method == 'POST':
		if form.is_valid() and form_parameters.is_valid() and form_commands.is_valid():
			task = form.save(commit=False)
			task.save()
			data['task'] = task
			task_save_formset(form_parameters, task)
			task_save_formset(form_commands, task)
			if task_id == None:
				return redirect(task.get_absolute_url())
			request.method = 'GET'
			form, form_parameters, form_commands = create_forms(request, task_id, args)
			request.method = 'POST'

	data['application'] = application
	data['is_new'] = task_id == None
	data['request'] = request
	data['form'] = form
	data['form_parameters'] = form_parameters
	data['form_commands'] = form_commands
	data['server_roles'] = ServerRole.objects.all()
	return render(request, 'page/task_form.html', data)

def create_forms(request, task_id, args):
	form = task_create_form('task', request, task_id, args)
	form_parameters = create_formset(request, TaskParameterFormset, task_id)
	form_commands = create_formset(request, TaskCommandFormset, task_id)
	return (form, form_parameters, form_commands)

def task_save_formset(formset, task):
	formset.save(commit=False)
	#for instance, _ in formset.changed_objects:
	for instance in formset.new_objects:
		instance.task_id = task.id
	for form in formset.ordered_forms:
		form.instance.order = form.cleaned_data['ORDER']
		form.instance.save()
	formset.save_m2m()

def create_formset(request, formset, parent_id):
	model = formset.model
	model_queryset = {
		'TaskParameter': model.objects.filter(task_id=parent_id).order_by('order'),
		'TaskCommand': model.objects.filter(task_id=parent_id).order_by('order')
	}
	if request.method == "POST":
		return formset(request.POST, 
			queryset=model_queryset[model.__name__], 
			prefix=model.__name__)
	else:
		return formset(queryset=model_queryset[model.__name__],
			prefix=model.__name__)

@login_required
def log_page(request, model_name, id):
	data = get_common_page_data(request)
	executions = Execution.objects
	if model_name == 'application':
		executions = executions.filter(environment__application_id=id)
		related = get_object_or_404(Application, pk=id)
	elif model_name == 'environment':
		executions = executions.filter(environment_id=id)
		related = get_object_or_404(Environment, pk=id)
	elif model_name == 'task':
		executions = executions.filter(task_id=id)
		related = get_object_or_404(Task, pk=id)
	elif model_name == 'user':
		executions = executions.filter(user_id=id)
		related = get_object_or_404(User, pk=id)
	else:
		raise Http404()
	for related_model in ['task', 'user', 'environment', 'parameters']:
		executions = executions.prefetch_related(related_model)
	data['executions'] = executions.order_by('-time_created')
	data['model_name'] = model_name
	data['related'] = related
	return render(request, 'page/log.html', data)

@login_required
def execution_page(request, execution_id):
	data = get_common_page_data(request)
	execution = get_object_or_404(Execution, pk=execution_id)
	data['execution'] = execution
	return render(request, 'page/execution.html', data)

@login_required
def task_delete(request, task_id):
	if request.method != 'POST':
		return Http404
	task = get_object_or_404(Task, pk=task_id)
	task.delete()
	data = {
		'status':True, 
		'action': 'redirect',
		'target': task.application.get_absolute_url()
		}
	return HttpResponse(json.dumps(data), content_type="application/json")

@login_required
def live_log(request, execution_id, last_id):
	data = ExecutionLiveLog.objects.filter(execution_id=execution_id, id__gt=last_id).order_by('id').values('id','event', 'data')
	return HttpResponse(json.dumps(list(data), cls=DjangoJSONEncoder), content_type="application/json")
