{{- define "questops.name" -}}
{{- .Chart.Name -}}
{{- end -}}

{{- define "questops.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "questops.labels" -}}
app.kubernetes.io/name: {{ include "questops.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "questops.selectorLabels" -}}
app.kubernetes.io/name: {{ include "questops.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "questops.secretName" -}}
{{- default (printf "%s-secrets" (include "questops.fullname" .)) .Values.app.existingSecret -}}
{{- end -}}

{{- define "questops.databaseUrl" -}}
{{- if .Values.database.url -}}
{{- .Values.database.url -}}
{{- else if .Values.database.builtin.enabled -}}
{{- printf "postgresql+psycopg2://questops:%s@%s-db:5432/questops" .Values.database.builtin.password (include "questops.fullname" .) -}}
{{- end -}}
{{- end -}}
