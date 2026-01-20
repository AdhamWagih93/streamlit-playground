{{- define "bsw.name" -}}
best-streamlit-website
{{- end -}}

{{- define "bsw.ns" -}}
{{ .Values.namespace | default "best-streamlit-website" }}
{{- end -}}

{{- define "bsw.labels" -}}
app.kubernetes.io/name: {{ include "bsw.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
