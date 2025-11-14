from rest_framework import serializers
from .models import User, Project, Task, ProjectAssignment


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False)
    can_access = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = [
            'id', 'email', 'username', 'name', 'role', 'password',
            'is_approved', 'approved_at', 'can_access',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at', 'is_approved', 'approved_at']
        extra_kwargs = {
            'password': {'write_only': True}
        }
    
    def get_can_access(self, obj):
        return obj.can_access_system
    
    def create(self, validated_data):
        password = validated_data.pop('password', None)
        validated_data['is_approved'] = False  # Pas approuvé par défaut
        user = User.objects.create(**validated_data)
        if password:
            user.set_password(password)
            user.save()
        return user
    
    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class ProjectAssignmentSerializer(serializers.ModelSerializer):
    """Serializer pour les assignations de projets"""
    user_name = serializers.CharField(source='user.name', read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)
    user_username = serializers.CharField(source='user.username', read_only=True)
    assigned_by_name = serializers.CharField(source='assigned_by.name', read_only=True)
    project_title = serializers.CharField(source='project.title', read_only=True)
    
    class Meta:
        model = ProjectAssignment
        fields = [
            'id', 'user', 'project', 'assigned_by', 'assigned_at',
            'can_edit', 'can_delete',
            'user_name', 'user_email', 'user_username',
            'assigned_by_name', 'project_title'
        ]
        read_only_fields = ['assigned_at']


class TaskSerializer(serializers.ModelSerializer):
    assigned_to_name = serializers.CharField(source='assigned_to.name', read_only=True)
    assigned_to_username = serializers.CharField(source='assigned_to.username', read_only=True)
    project_title = serializers.CharField(source='project.title', read_only=True)
    
    class Meta:
        model = Task
        fields = '__all__'


class ProjectSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    assignments = ProjectAssignmentSerializer(source='projectassignment_set', many=True, read_only=True)
    tasks = TaskSerializer(many=True, read_only=True)
    tasks_count = serializers.SerializerMethodField()
    members_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Project
        fields = [
            'id', 'title', 'description', 'created_by', 'created_at', 'updated_at', 'is_active',
            'created_by_name', 'created_by_username',
            'assignments', 'tasks', 'tasks_count', 'members_count'
        ]
        read_only_fields = ['created_at', 'updated_at']
    
    def get_tasks_count(self, obj):
        return obj.tasks.count()
    
    def get_members_count(self, obj):
        return obj.assigned_users.count()