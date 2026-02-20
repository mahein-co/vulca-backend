# chatbot/services/export_service.py

import xlsxwriter
import io
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.units import cm

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
        sheet.write(1, 0, f"Généré le: {datetime.now().strftime('%d/%m/%Y %H:%M')}", workbook.add_format({'italic': True, 'font_size': 10}))
        
        row = 3
        
        # Logique Bilan / États Financiers
        if "actif" in data or "passif_equity" in data or "bilan" in data:
            bilan_data = data.get("bilan", data) # Support direct ou imbriqué
            
            sheet.write(row, 0, "Catégorie / Compte", header_format)
            sheet.write(row, 1, "Libellé", header_format)
            sheet.write(row, 2, "Montant", header_format)
            row += 1
            
            # ACTIF
            if "actif" in bilan_data:
                sheet.merge_range(row, 0, row, 2, "ACTIF", category_format)
                row += 1
                for cat, items in bilan_data["actif"].items():
                    sheet.write(row, 0, cat, workbook.add_format({'italic': True, 'bold': True, 'font_color': '#334155'}))
                    row += 1
                    if isinstance(items, list):
                        for item in items:
                            sheet.write(row, 0, item.get("compte", ""))
                            sheet.write(row, 1, item.get("libelle", ""))
                            sheet.write(row, 2, item.get("montant", 0), currency_format)
                            row += 1
                
                sheet.write(row, 1, "TOTAL ACTIF", total_format)
                sheet.write(row, 2, bilan_data.get("totals", {}).get("total_actif", 0), total_format)
                row += 2
            
            # PASSIF
            if "passif_equity" in bilan_data:
                sheet.merge_range(row, 0, row, 2, "PASSIF & CAPITAUX PROPRES", category_format)
                row += 1
                for cat, items in bilan_data["passif_equity"].items():
                    sheet.write(row, 0, cat, workbook.add_format({'italic': True, 'bold': True, 'font_color': '#334155'}))
                    row += 1
                    if isinstance(items, list):
                        for item in items:
                            sheet.write(row, 0, item.get("compte", ""))
                            sheet.write(row, 1, item.get("libelle", ""))
                            sheet.write(row, 2, item.get("montant", 0), currency_format)
                            row += 1
                
                sheet.write(row, 1, "TOTAL PASSIF & CAPITAUX PROPRES", total_format)
                sheet.write(row, 2, (bilan_data.get("totals", {}).get("total_passif", 0) + bilan_data.get("totals", {}).get("total_equity", 0)), total_format)
                row += 2

        # Logique Compte de Résultat
        if any(k in data for k in ["resultat", "produits", "compte_de_resultat"]):
            res_data = data.get("compte_de_resultat", data.get("resultat", data))
            sheet.merge_range(row, 0, row, 2, "COMPTE DE RÉSULTAT", category_format)
            row += 1
            if isinstance(res_data, dict):
                 # Détails si présents dans 'details', sinon cherche à la racine
                 details = res_data.get("details", res_data)
                 for key in ["produits", "charges"]:
                      items = details.get(key, [])
                      if isinstance(items, list) and items:
                          sheet.write(row, 0, key.upper(), workbook.add_format({'bold': True}))
                          row += 1
                          for item in items:
                              sheet.write(row, 0, item.get("compte", ""))
                              sheet.write(row, 1, item.get("libelle", ""))
                              sheet.write(row, 2, item.get("montant", 0), currency_format)
                              row += 1
                 row += 1
                 res_val = res_data.get("montant", res_data.get("resultat_net", 0))
                 sheet.write(row, 1, "RÉSULTAT NET", total_format)
                 try:
                     sheet.write(row, 2, float(res_val), total_format)
                 except (TypeError, ValueError):
                     sheet.write(row, 2, 0.0, total_format)

        # Logique Comparatif
        elif "annee_1" in data and "evolution" in data:
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
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
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

        # Logique Bilan
        if "actif" in data or "passif_equity" in data or "bilan" in data:
            bilan_data = data.get("bilan", data)
            
            # ACTIF
            if "actif" in bilan_data:
                elements.append(Paragraph("BILAN - ACTIF", section_style))
                table_data = [["Compte", "Libellé", "Montant (Ar)"]]
                for cat, items in bilan_data["actif"].items():
                    if isinstance(items, list):
                        table_data.append([Paragraph(f"<b>{cat}</b>", styles['Normal']), "", ""])
                        for item in items:
                            table_data.append([
                                str(item.get("compte", "")),
                                item.get("libelle", "")[:40],
                                f"{item.get('montant', 0):,.2f}".replace(",", " ")
                            ])
                
                table_data.append(["", Paragraph("<b>TOTAL ACTIF</b>", styles['Normal']), f"<b>{bilan_data.get('totals', {}).get('total_actif', 0):,.2f}</b>".replace(",", " ")])
                
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

            # PASSIF
            if "passif_equity" in bilan_data:
                elements.append(Paragraph("BILAN - PASSIF & CAPITAUX PROPRES", section_style))
                table_data = [["Compte", "Libellé", "Montant (Ar)"]]
                for cat, items in bilan_data["passif_equity"].items():
                    if isinstance(items, list):
                        table_data.append([Paragraph(f"<b>{cat}</b>", styles['Normal']), "", ""])
                        for item in items:
                            table_data.append([
                                str(item.get("compte", "")),
                                item.get("libelle", "")[:40],
                                f"{item.get('montant', 0):,.2f}".replace(",", " ")
                            ])
                
                tot_p = bilan_data.get('totals', {}).get('total_passif', 0)
                tot_e = bilan_data.get('totals', {}).get('total_equity', 0)
                table_data.append(["", Paragraph("<b>TOTAL PASSIF & C.P</b>", styles['Normal']), f"<b>{(tot_p + tot_e):,.2f}</b>".replace(",", " ")])
                
                t = Table(table_data, colWidths=[3*cm, 9*cm, 4*cm])
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F8FAFC')),
                    ('TEXTCOLOR', (0,0), (-1,0), colors.HexColor('#1E293B')),
                    ('ALIGN', (2,0), (2,-1), 'RIGHT'),
                    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                    ('BOTTOMPADDING', (0,0), (-1,0), 12),
                    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
                ]))
                elements.append(t)

        # Logique Résultat
        if any(k in data for k in ["resultat_net", "resultat", "produits", "compte_de_resultat"]):
            res_data = data.get("compte_de_resultat", data.get("resultat", data))
            elements.append(PageBreak() if len(elements) > 10 else Spacer(1, 1*cm))
            elements.append(Paragraph("COMPTE DE RÉSULTAT", section_style))
            
            if isinstance(res_data, dict):
                table_data = [["Rubrique", "Détail", "Montant (Ar)"]]
                details = res_data.get("details", res_data)
                for key in ["produits", "charges"]:
                    items = details.get(key, [])
                    if isinstance(items, list) and items:
                        table_data.append([Paragraph(f"<b>{key.upper()}</b>", styles['Normal']), "", ""])
                        for item in items:
                            table_data.append([
                                str(item.get("compte", "")),
                                item.get("libelle", "")[:40],
                                f"{item.get('montant', 0):,.2f}".replace(",", " ")
                            ])
                
                res_val = res_data.get("montant", res_data.get("resultat_net", 0))
                try:
                    res_val_f = float(res_val)
                except (TypeError, ValueError):
                    res_val_f = 0.0
                
                table_data.append(["", Paragraph("<b>RÉSULTAT NET</b>", styles['Normal']), f"<b>{res_val_f:,.2f}</b>".replace(",", " ")])
                t = Table(table_data, colWidths=[3*cm, 9*cm, 4*cm])
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F8FAFC')),
                    ('ALIGN', (2,0), (2,-1), 'RIGHT'),
                    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
                ]))
                elements.append(t)

        # Logique Comparatif
        elif "annee_1" in data and "evolution" in data:
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

        # Génération
        doc.build(elements)
        buffer.seek(0)
        return buffer
