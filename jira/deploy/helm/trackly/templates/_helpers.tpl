{{/* Common naming + labels */}}

{{- define "trackly.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "trackly.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "trackly.backend.fullname" -}}{{ include "trackly.fullname" . }}-backend{{- end -}}
{{- define "trackly.frontend.fullname" -}}{{ include "trackly.fullname" . }}-frontend{{- end -}}
{{- define "trackly.postgres.fullname" -}}{{ include "trackly.fullname" . }}-postgres{{- end -}}

{{- define "trackly.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ include "trackly.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: trackly
{{- end -}}

{{- define "trackly.selectorLabels" -}}
app.kubernetes.io/name: {{ include "trackly.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "trackly.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "trackly.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* Name of the Secret holding SECRET_KEY / passwords */}}
{{- define "trackly.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- include "trackly.fullname" . -}}
{{- end -}}
{{- end -}}

{{/* Image tags default to the chart appVersion */}}
{{- define "trackly.backend.image" -}}
{{- $reg := .Values.image.registry -}}
{{- $repo := .Values.image.backend.repository -}}
{{- $tag := default .Chart.AppVersion .Values.image.backend.tag -}}
{{- if $reg }}{{ printf "%s/%s:%s" $reg $repo $tag }}{{ else }}{{ printf "%s:%s" $repo $tag }}{{ end }}
{{- end -}}

{{- define "trackly.frontend.image" -}}
{{- $reg := .Values.image.registry -}}
{{- $repo := .Values.image.frontend.repository -}}
{{- $tag := default .Chart.AppVersion .Values.image.frontend.tag -}}
{{- if $reg }}{{ printf "%s/%s:%s" $reg $repo $tag }}{{ else }}{{ printf "%s:%s" $repo $tag }}{{ end }}
{{- end -}}

{{/* Effective database host: bundled postgres service, or external host */}}
{{- define "trackly.databaseHost" -}}
{{- if .Values.postgresql.enabled -}}
{{- include "trackly.postgres.fullname" . -}}
{{- else -}}
{{- required "externalDatabase.host is required when postgresql.enabled=false" .Values.externalDatabase.host -}}
{{- end -}}
{{- end -}}

{{- define "trackly.databasePort" -}}
{{- if .Values.postgresql.enabled }}5432{{ else }}{{ .Values.externalDatabase.port | default 5432 }}{{ end -}}
{{- end -}}

{{- define "trackly.databaseName" -}}
{{- if .Values.postgresql.enabled }}{{ .Values.postgresql.auth.database }}{{ else }}{{ .Values.externalDatabase.database }}{{ end -}}
{{- end -}}

{{- define "trackly.databaseUser" -}}
{{- if .Values.postgresql.enabled }}{{ .Values.postgresql.auth.username }}{{ else }}{{ .Values.externalDatabase.username }}{{ end -}}
{{- end -}}
