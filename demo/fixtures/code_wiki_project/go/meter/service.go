package meter

func (m *Meter) Record() {
	helper()
	m.Reset()
}

func Use(m *Meter) {
	m.Reset()
}
