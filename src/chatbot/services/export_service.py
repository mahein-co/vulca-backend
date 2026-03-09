# chatbot/services/export_service.py

import xlsxwriter
import io
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.units import cm
import re


def _clean_html(text):
    """Supprime les balises HTML d'une chaîne (ex: <b>150.00</b> -> 150.00)."""
    if not isinstance(text, str):
        return text
    return re.sub(r'<[^>]*>', '', text).strip()


def _format_montant(value):
    """Format montant avec espace comme séparateur de milliers (ex: 10 000 000.00)."""
    if value is None:
        return "0.00"
    try:
        f = float(value)
        return f"{f:,.2f}".replace(",", " ")
    except (TypeError, ValueError):
        return "0.00"


def _safe_float(value, default=0.0):
    if value is None:
        return default
    if isinstance(value, str):
        # Nettoyage : enlever <b>, espaces, insécables
        val = _clean_html(value)
        val = val.replace(' ', '').replace('\xa0', '').replace(',', '.')
        try:
            return float(val)
        except (TypeError, ValueError):
            return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_export_data(data):
    """
    Normalise les données pour l'export : supporte data avec 'bilan' imbriqué ou structure directe.
    Retourne (bilan_data, compte_resultat_data) ou (None, None) en cas d'erreur.
    """
    if not data or not isinstance(data, dict):
        return None, None
    if data.get("error"):
        return None, None
    bilan_data = data.get("bilan", data if ("actif" in data or "passif" in data or "passif_equity" in data) else None)
    
    # Robustesse : si BilanSummary a été utilisé, les données sont dans 'details'
    if isinstance(bilan_data, dict) and "details" in bilan_data:
        nested = bilan_data["details"]
        return nested, None, None

    if isinstance(bilan_data, dict) and bilan_data.get("error"):
        bilan_data = None
        
    res_data = data.get("compte_de_resultat", data.get("resultat", data.get("resultat_structuré", data if "total_produits" in data or "resultat_net" in data else None)))
    comp_data = data.get("comparaison", data if "annee_1" in data and "evolution" in data else None)
    return bilan_data, res_data, comp_data


class ExportService:
    """
    Service pour générer des rapports professionnels (Excel & PDF) pour REKAPY.
    """

    @staticmethod
    def generate_excel_report(data, report_type="Rapport Financier"):
        """
        Génère un buffer Excel moderne à partir des données structurées.
        """
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        sheet = workbook.add_worksheet(report_type[:31])

        # Formats modernes (Slate theme)
        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#1E293B', # Slate 800
            'font_color': 'white',
            'border': 1,
            'align': 'center',
            'valign': 'vcenter',
            'font_name': 'Arial'
        })
        
        category_format = workbook.add_format({
            'bold': True,
            'bg_color': '#F1F5F9', # Slate 100
            'border': 1,
            'font_name': 'Arial'
        })
        
        currency_format = workbook.add_format({
            'num_format': '#,##0.00 "Ar"',
            'border': 1,
            'font_name': 'Arial'
        })
        
        total_format = workbook.add_format({
            'bold': True,
            'bg_color': '#E2E8F0', # Slate 200
            'border': 1,
            'num_format': '#,##0.00 "Ar"',
            'font_name': 'Arial'
        })

        title_format = workbook.add_format({
            'bold': True,
            'font_size': 16,
            'font_color': '#0F172A',
            'font_name': 'Arial'
        })

        # Titre et Header
        sheet.write(0, 0, f"REKAPY - {report_type}", title_format)
        sheet.write(1, 0, f"Généré le: {datetime.now().strftime('%d/%m/%Y à %H:%M')}", workbook.add_format({'italic': True, 'font_size': 10}))
        
        row = 3
        
        bilan_data, res_data, comp_data = _normalize_export_data(data)
        
        # Logique Bilan / États Financiers (structure comme les PDFs de référence)
        if bilan_data and ("actif" in bilan_data or "passif" in bilan_data or "passif_equity" in bilan_data):
            # En-têtes identiques au PDF : Compte | Libellé | Montant (Ar)
            sheet.write(row, 0, "Compte", header_format)
            sheet.write(row, 1, "Libellé", header_format)
            sheet.write(row, 2, "Montant (Ar)", header_format)
            row += 1
            
            # ACTIF
            if "actif" in bilan_data:
                sheet.merge_range(row, 0, row, 2, "BILAN - ACTIF", category_format)
                row += 1
                for cat, items in bilan_data["actif"].items():
                    sheet.write(row, 0, _clean_html(cat), workbook.add_format({'italic': True, 'bold': True, 'font_color': '#334155'}))
                    row += 1
                    if isinstance(items, list):
                        for item in items:
                            sheet.write(row, 0, _clean_html(str(item.get("numero_compte", item.get("compte", "")))))
                            sheet.write(row, 1, _clean_html(str(item.get("libelle", ""))))
                            sheet.write(row, 2, _safe_float(item.get("montant")), currency_format)
                            row += 1
                
                total_actif = _safe_float(bilan_data.get("total_actif", bilan_data.get("totals", {}).get("total_actif")))
                sheet.write(row, 0, "")
                sheet.write(row, 1, "TOTAL ACTIF", total_format)
                sheet.write(row, 2, total_actif, total_format)
                row += 2
            
            # PASSIF & CAPITAUX PROPRES
            passif_key = "passif" if "passif" in bilan_data else "passif_equity"
            if passif_key in bilan_data:
                sheet.merge_range(row, 0, row, 2, "BILAN - PASSIF & CAPITAUX PROPRES", category_format)
                row += 1
                for cat, items in bilan_data[passif_key].items():
                    sheet.write(row, 0, _clean_html(cat), workbook.add_format({'italic': True, 'bold': True, 'font_color': '#334155'}))
                    row += 1
                    if isinstance(items, list):
                        for item in items:
                            sheet.write(row, 0, _clean_html(str(item.get("numero_compte", item.get("compte", "")))))
                            sheet.write(row, 1, _clean_html(str(item.get("libelle", ""))))
                            sheet.write(row, 2, _safe_float(item.get("montant")), currency_format)
                            row += 1
                
                total_pe = _safe_float(bilan_data.get("total_passif", 0))
                if total_pe == 0:
                    tot_p = _safe_float(bilan_data.get("totals", {}).get("total_passif"))
                    tot_e = _safe_float(bilan_data.get("totals", {}).get("total_equity"))
                    total_pe = tot_p + tot_e
                
                sheet.write(row, 0, "")
                sheet.write(row, 1, "TOTAL PASSIF & C.P", total_format)
                sheet.write(row, 2, total_pe, total_format)
                row += 2

        # Logique Compte de Résultat
        if res_data and isinstance(res_data, dict):
            sheet.merge_range(row, 0, row, 2, "COMPTE DE RÉSULTAT", category_format)
            row += 1
            sheet.write(row, 0, "Rubrique", header_format)
            sheet.write(row, 1, "Détail", header_format)
            sheet.write(row, 2, "Montant (Ar)", header_format)
            row += 1
            details = res_data.get("details", res_data)
            for key in ["produits", "charges"]:
                items = details.get(key, []) if isinstance(details, dict) else []
                if isinstance(items, list) and items:
                    sheet.write(row, 0, key.upper(), workbook.add_format({'bold': True}))
                    row += 1
                    for item in items:
                        sheet.write(row, 0, _clean_html(str(item.get("numero_compte", item.get("compte", "")))))
                        sheet.write(row, 1, _clean_html(str(item.get("libelle", ""))))
                        sheet.write(row, 2, _safe_float(item.get("montant")), currency_format)
                        row += 1
            row += 1
            res_val = _safe_float(res_data.get("montant", res_data.get("resultat_net")))
            sheet.write(row, 0, "")
            sheet.write(row, 1, "RÉSULTAT NET", total_format)
            sheet.write(row, 2, res_val, total_format)

        # Logique Comparatif
        elif comp_data and "annee_1" in comp_data and "evolution" in comp_data:
            data = comp_data # Utiliser les données normalisées pour la suite de ce bloc
            sheet.write(row, 0, "Indicateur", header_format)
            sheet.write(row, 1, f"Année {data['annee_1']['annee']}", header_format)
            sheet.write(row, 2, f"Année {data['annee_2']['annee']}", header_format)
            sheet.write(row, 3, "Variation Absolue", header_format)
            sheet.write(row, 4, "Variation %", header_format)
            row += 1
            
            indicators = [
                ("Chiffre d'Affaires", "ca"),
                ("Charges", "charges"),
                ("Résultat Net", "resultat")
            ]
            
            for label, key in indicators:
                val1 = data["annee_1"].get("chiffre_affaires", 0) if key == "ca" else data["annee_1"].get(key, 0)
                val2 = data["annee_2"].get("chiffre_affaires", 0) if key == "ca" else data["annee_2"].get(key, 0)
                var_abs = data["evolution"].get(key, 0)
                var_pct = data["evolution"].get(f"{key}_pct", 0)
                
                sheet.write(row, 0, label)
                sheet.write(row, 1, val1, currency_format)
                sheet.write(row, 2, val2, currency_format)
                sheet.write(row, 3, var_abs, currency_format)
                sheet.write(row, 4, var_pct / 100 if var_pct else 0, workbook.add_format({'num_format': '0.00%', 'border': 1}))
                row += 1
                
            if "analyse" in data:
                row += 2
                sheet.merge_range(row, 0, row, 4, "Analyse Stratégique", category_format)
                row += 1
                sheet.merge_range(row, 0, row + 4, 4, data["analyse"], workbook.add_format({'text_wrap': True, 'valign': 'top', 'border': 1}))

        # Mise en forme colonnes
        sheet.set_column(0, 0, 25)
        sheet.set_column(1, 1, 45)
        sheet.set_column(2, 4, 18)
        
        workbook.close()
        output.seek(0)
        return output

    @staticmethod
    def generate_pdf_report(data, report_type="Rapport Financier"):
        """
        Génère un PDF moderne et structuré avec ReportLab.
        """
        buffer = io.BytesIO()
        
        def _pdf_footer(canvas, doc):
            """Pied de page structuré type REKAPY : -- Page N --"""
            canvas.saveState()
            page_num = canvas.getPageNumber()
            canvas.setFont("Helvetica", 9)
            canvas.setFillColor(colors.HexColor("#64748B"))
            canvas.drawCentredString(A4[0] / 2, 1.5 * cm, f"-- {page_num} --")
            canvas.restoreState()
        
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=2*cm,
            leftMargin=2*cm,
            topMargin=2*cm,
            bottomMargin=2.5*cm,
            onFirstPage=_pdf_footer,
            onLaterPages=_pdf_footer,
        )
        elements = []
        styles = getSampleStyleSheet()

        # Styles personnalisés
        title_style = ParagraphStyle(
            'RekapyTitle',
            parent=styles['Heading1'],
            fontSize=22,
            textColor=colors.HexColor('#1E293B'),
            alignment=1, # Center
            spaceAfter=20,
            fontName='Helvetica-Bold'
        )
        
        subtitle_style = ParagraphStyle(
            'RekapySubtitle',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#64748B'),
            alignment=1,
            spaceAfter=30
        )

        section_style = ParagraphStyle(
            'RekapySection',
            parent=styles['Heading2'],
            fontSize=14,
            textColor=colors.white,
            backColor=colors.HexColor('#334155'),
            leftIndent=0,
            spaceBefore=15,
            spaceAfter=10,
            borderPadding=5
        )

        # Header
        elements.append(Paragraph(f"REKAPY - {report_type}", title_style))
        elements.append(Paragraph(f"Généré le: {datetime.now().strftime('%d/%m/%Y à %H:%M')}", subtitle_style))

        bilan_data, res_data, comp_data = _normalize_export_data(data)

        # Logique Bilan (structure identique aux PDFs de référence)
        if bilan_data and ("actif" in bilan_data or "passif" in bilan_data or "passif_equity" in bilan_data):
            if "actif" in bilan_data:
                elements.append(Paragraph("BILAN - ACTIF", section_style))
                table_data = [["Compte", "Libellé", "Montant (Ar)"]]
                for cat, items in bilan_data["actif"].items():
                    if isinstance(items, list):
                        table_data.append([Paragraph(f"<b>{cat}</b>", styles['Normal']), "", ""])
                        for item in items:
                            libelle = (item.get("libelle") or "")[:50]
                            table_data.append([
                                str(item.get("numero_compte", item.get("compte", ""))),
                                libelle,
                                _format_montant(item.get("montant"))
                            ])
                
                total_actif = _format_montant(bilan_data.get("total_actif", bilan_data.get("totals", {}).get("total_actif")))
                table_data.append(["", Paragraph("<b>TOTAL ACTIF</b>", styles['Normal']), f"<b>{total_actif}</b>"])
                
                t = Table(table_data, colWidths=[3*cm, 9*cm, 4*cm])
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F8FAFC')),
                    ('TEXTCOLOR', (0,0), (-1,0), colors.HexColor('#1E293B')),
                    ('ALIGN', (2,0), (2,-1), 'RIGHT'),
                    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                    ('BOTTOMPADDING', (0,0), (-1,0), 12),
                    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
                    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ]))
                elements.append(t)
                elements.append(Spacer(1, 1*cm))

            # PASSIF & CAPITAUX PROPRES
            passif_key = "passif" if "passif" in bilan_data else "passif_equity"
            if passif_key in bilan_data:
                elements.append(Paragraph("BILAN - PASSIF & CAPITAUX PROPRES", section_style))
                table_data = [["Compte", "Libellé", "Montant (Ar)"]]
                for cat, items in bilan_data[passif_key].items():
                    if isinstance(items, list):
                        table_data.append([Paragraph(f"<b>{cat}</b>", styles['Normal']), "", ""])
                        for item in items:
                            libelle = (item.get("libelle") or "")[:50]
                            table_data.append([
                                str(item.get("numero_compte", item.get("compte", ""))),
                                libelle,
                                _format_montant(item.get("montant"))
                            ])
                
                total_pe_val = _safe_float(bilan_data.get("total_passif", 0))
                if total_pe_val == 0:
                    tot_p = _safe_float(bilan_data.get('totals', {}).get('total_passif'))
                    tot_e = _safe_float(bilan_data.get('totals', {}).get('total_equity'))
                    total_pe_val = tot_p + tot_e
                
                total_pe = _format_montant(total_pe_val)
                table_data.append(["", Paragraph("<b>TOTAL PASSIF & C.P</b>", styles['Normal']), f"<b>{total_pe}</b>"])
                
                t = Table(table_data, colWidths=[3*cm, 9*cm, 4*cm])
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F8FAFC')),
                    ('TEXTCOLOR', (0,0), (-1,0), colors.HexColor('#1E293B')),
                    ('ALIGN', (2,0), (2,-1), 'RIGHT'),
                    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                    ('BOTTOMPADDING', (0,0), (-1,0), 12),
                    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
                    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ]))
                elements.append(t)

        # Logique Compte de Résultat
        if res_data and isinstance(res_data, dict):
            elements.append(PageBreak() if len(elements) > 10 else Spacer(1, 1*cm))
            elements.append(Paragraph("COMPTE DE RÉSULTAT", section_style))
            table_data = [["Rubrique", "Détail", "Montant (Ar)"]]
            details = res_data.get("details", res_data)
            if isinstance(details, dict):
                for key in ["produits", "charges"]:
                    items = details.get(key, [])
                    if isinstance(items, list) and items:
                        table_data.append([Paragraph(f"<b>{key.upper()}</b>", styles['Normal']), "", ""])
                        for item in items:
                            libelle = (item.get("libelle") or "")[:50]
                            table_data.append([
                                str(item.get("numero_compte", item.get("compte", ""))),
                                libelle,
                                _format_montant(item.get("montant"))
                            ])
            
            res_val_f = _safe_float(res_data.get("montant", res_data.get("resultat_net")))
            table_data.append(["", Paragraph("<b>RÉSULTAT NET</b>", styles['Normal']), f"<b>{_format_montant(res_val_f)}</b>"])
            t = Table(table_data, colWidths=[3*cm, 9*cm, 4*cm])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F8FAFC')),
                ('ALIGN', (2,0), (2,-1), 'RIGHT'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ]))
            elements.append(t)

        # Logique Comparatif
        elif comp_data and "annee_1" in comp_data and "evolution" in comp_data:
            data = comp_data # Utiliser les données normalisées
            elements.append(Paragraph("ANALYSE COMPARATIVE", section_style))
            table_data = [
                ["Indicateur", f"Année {data['annee_1']['annee']}", f"Année {data['annee_2']['annee']}", "Variation"]
            ]
            for label, key in [("C.A", "ca"), ("Charges", "charges"), ("Résultat", "resultat")]:
                val1 = data["annee_1"].get("chiffre_affaires", 0) if key == "ca" else data["annee_1"].get(key, 0)
                val2 = data["annee_2"].get("chiffre_affaires", 0) if key == "ca" else data["annee_2"].get(key, 0)
                var_pct = data["evolution"].get(f"{key}_pct", 0)
                table_data.append([
                    label,
                    f"{val1:,.2f}".replace(",", " "),
                    f"{val2:,.2f}".replace(",", " "),
                    f"{var_pct:+.1f}%"
                ])
            
            t = Table(table_data, colWidths=[4*cm, 4*cm, 4*cm, 4*cm])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F8FAFC')),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
                ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
            ]))
            elements.append(t)
            
            if "analyse" in data:
                elements.append(Spacer(1, 1*cm))
                elements.append(Paragraph("ANALYSE COMPTABLE", styles['Heading3']))
                elements.append(Paragraph(data["analyse"], styles['Normal']))

        # Si aucun contenu structuré (données manquantes ou erreur)
        if len(elements) <= 2:
            elements.append(Paragraph(
                "Aucune donnée disponible pour la période demandée. Veuillez préciser une année ou une période.",
                styles['Normal']
            ))

        # Génération
        doc.build(elements)
        buffer.seek(0)
        return buffer
